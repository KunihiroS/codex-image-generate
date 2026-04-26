#!/usr/bin/env python3
"""Generate images through the Codex/ChatGPT Responses image_generation route.

This is a small personal CLI helper. It reads a Codex ChatGPT OAuth access token
from ~/.codex/auth.json by default, or from CODEX_ACCESS_TOKEN, and never prints
or stores token values.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_AUTH_FILE = Path.home() / ".codex" / "auth.json"
DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_RESPONSES_MODEL = "gpt-5.5"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_SIZE = "2048x1152"
DEFAULT_QUALITY = "high"
DEFAULT_FORMAT = "png"
DEFAULT_TIMEOUT = 300

MAX_SSE_BYTES = 64 * 1024 * 1024
MAX_SSE_EVENTS = 512
MAX_IMAGE_BASE64_CHARS = 64 * 1024 * 1024

SUPPORTED_SIZES = {
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "2048x1152",
    "3840x2160",
    "2160x3840",
}
SUPPORTED_QUALITIES = {"low", "medium", "high"}
SUPPORTED_FORMATS = {"png", "jpeg", "webp"}

EXIT_LOCAL_VALIDATION = 1
EXIT_AUTH = 2
EXIT_HTTP = 3
EXIT_API_FAILURE = 4
EXIT_NO_IMAGE = 5
EXIT_DECODE_OR_WRITE = 6


class UserFacingError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def redact(text: str) -> str:
    # Conservative redaction for accidental bearer/token-like snippets in errors.
    redacted = text
    for key in ("access_token", "refresh_token", "id_token", "OPENAI_API_KEY", "account_id"):
        redacted = redacted.replace(key, f"[{key.upper()}]")
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", redacted, flags=re.I)
    redacted = re.sub(r"eyJ[A-Za-z0-9._\-]{20,}", "[REDACTED_JWT]", redacted)
    redacted = re.sub(r"[A-Za-z0-9_\-]{48,}", "[REDACTED_LONG_TOKEN]", redacted)
    return redacted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an image via the Codex/ChatGPT image_generation route."
    )
    parser.add_argument("--prompt", default=None, help="Prompt text file path")
    parser.add_argument("--output", default="codex-image-output.png", help="Output image path")
    parser.add_argument("--size", default=DEFAULT_SIZE, choices=sorted(SUPPORTED_SIZES), help="Image size")
    parser.add_argument("--quality", default=DEFAULT_QUALITY, choices=sorted(SUPPORTED_QUALITIES), help="Image generation quality")
    parser.add_argument("--format", default=DEFAULT_FORMAT, choices=sorted(SUPPORTED_FORMATS), help="Output format")
    parser.add_argument("--auth-file", default=None, help="Codex auth JSON path. Defaults to CODEX_AUTH_FILE or ~/.codex/auth.json")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Codex backend base URL")
    parser.add_argument("--responses-model", default=DEFAULT_RESPONSES_MODEL, help="Responses main model")
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL, help="image_generation tool model")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT, type=int, help="HTTP timeout seconds")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it already exists")
    parser.add_argument("--save-sse", action="store_true", help="Save raw SSE response next to output for debugging")
    parser.add_argument("--metadata", default=None, help="Optional metadata JSON output path")
    parser.add_argument("--verbose", action="store_true", help="Print extra non-secret diagnostics")
    parser.add_argument("--check-auth", action="store_true", help="Validate auth source and exit without sending a request")
    return parser.parse_args()


def auth_file_from_args(args: argparse.Namespace) -> Path:
    raw = args.auth_file or os.environ.get("CODEX_AUTH_FILE")
    return Path(raw).expanduser() if raw else DEFAULT_AUTH_FILE


def load_auth(args: argparse.Namespace) -> tuple[str, str | None, str]:
    env_token = os.environ.get("CODEX_ACCESS_TOKEN")
    if env_token:
        return env_token, os.environ.get("CODEX_ACCOUNT_ID"), "env:CODEX_ACCESS_TOKEN"

    path = auth_file_from_args(args)
    if not path.exists():
        raise UserFacingError(
            f"Codex auth file not found: {path}\n"
            "Run `codex login` and confirm `codex login status` says `Logged in using ChatGPT`, "
            "or set CODEX_ACCESS_TOKEN.",
            EXIT_AUTH,
        )
    try:
        auth = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise UserFacingError(f"Codex auth file is not valid JSON: {path}: {exc}", EXIT_AUTH)
    except OSError as exc:
        raise UserFacingError(f"Could not read Codex auth file: {path}: {exc}", EXIT_AUTH)

    auth_mode = auth.get("auth_mode")
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise UserFacingError(
            f"Codex auth file has no tokens object: {path}\nRun `codex login` again.",
            EXIT_AUTH,
        )
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise UserFacingError(
            f"Codex auth file has no access token: {path}\nRun `codex login` again.",
            EXIT_AUTH,
        )
    account_id = tokens.get("account_id")
    if account_id is not None and not isinstance(account_id, str):
        account_id = None
    source = f"auth-file:{path}"
    if auth_mode and auth_mode != "chatgpt":
        eprint(f"WARNING: auth_mode is {auth_mode!r}, expected 'chatgpt'. Continuing without printing secrets.")
    return token, account_id, source


def validate_local_inputs(args: argparse.Namespace) -> tuple[Path, Path, str]:
    if args.timeout <= 0:
        raise UserFacingError("--timeout must be a positive integer", EXIT_LOCAL_VALIDATION)
    if not args.prompt:
        raise UserFacingError("--prompt is required unless --check-auth is used", EXIT_LOCAL_VALIDATION)
    prompt_path = Path(args.prompt).expanduser()
    output_path = Path(args.output).expanduser()
    if not prompt_path.exists():
        raise UserFacingError(f"Prompt file not found: {prompt_path}", EXIT_LOCAL_VALIDATION)
    if not prompt_path.is_file():
        raise UserFacingError(f"Prompt path is not a file: {prompt_path}", EXIT_LOCAL_VALIDATION)
    try:
        prompt = prompt_path.read_text()
    except OSError as exc:
        raise UserFacingError(f"Could not read prompt file: {prompt_path}: {exc}", EXIT_LOCAL_VALIDATION)
    if not prompt.strip():
        raise UserFacingError(f"Prompt file is empty: {prompt_path}", EXIT_LOCAL_VALIDATION)
    if output_path.exists() and not args.overwrite:
        raise UserFacingError(
            f"Output file already exists: {output_path}\nUse --overwrite to replace it.",
            EXIT_LOCAL_VALIDATION,
        )
    ext = output_path.suffix.lstrip(".").lower()
    format_to_ext = {"jpeg": "jpg", "png": "png", "webp": "webp"}
    expected_ext = format_to_ext.get(args.format, args.format)
    if ext and ext not in (args.format, expected_ext):
        eprint(f"WARNING: --format {args.format} but output extension is .{ext}. File content will be {args.format}.")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserFacingError(f"Could not create output directory: {output_path.parent}: {exc}", EXIT_LOCAL_VALIDATION)
    return prompt_path, output_path, prompt


def build_request(args: argparse.Namespace, prompt: str, token: str, account_id: str | None) -> urllib.request.Request:
    base_url = args.base_url.rstrip("/")
    url = f"{base_url}/responses"
    body = {
        "model": args.responses_model,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "instructions": "You are a professional image generation assistant. Generate exactly one image. Do not return secrets or credentials.",
        "tools": [
            {
                "type": "image_generation",
                "model": args.image_model,
                "size": args.size,
                "quality": args.quality,
                "output_format": args.format,
            }
        ],
        "tool_choice": {"type": "image_generation"},
        "stream": True,
        "store": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "codex-image-generate/0.1",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")[:4000]
    except Exception:
        body = ""
    hint = {
        401: "Authentication failed or token expired. Run `codex login status`; if needed, run `codex login` again.",
        403: "Forbidden. The account may not have access to this Codex/ChatGPT image route or the request may be blocked.",
        429: "Rate limited or quota constrained. Wait and retry manually if appropriate.",
        500: "Server error from backend. Retry later if appropriate.",
        502: "Bad gateway from backend. Retry later if appropriate.",
        503: "Service unavailable from backend. Retry later if appropriate.",
        504: "Gateway timeout from backend. Retry later or increase --timeout.",
    }.get(exc.code, "HTTP request failed.")
    msg = f"HTTP_ERROR {exc.code} {exc.reason}\n{hint}"
    if body:
        msg += f"\nResponse body preview:\n{redact(body)}"
    return msg


def read_http_response(req: urllib.request.Request, timeout: int) -> tuple[int, str | None, str]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SSE_BYTES:
                    raise UserFacingError("Codex image response exceeded 64MB SSE limit", EXIT_API_FAILURE)
                chunks.append(chunk)
            return resp.status, resp.headers.get("content-type"), b"".join(chunks).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise UserFacingError(http_error_message(exc), EXIT_HTTP)
    except TimeoutError as exc:
        raise UserFacingError(f"Request timed out after {timeout}s: {exc}", EXIT_HTTP)
    except urllib.error.URLError as exc:
        raise UserFacingError(f"Network error: {redact(str(exc))}", EXIT_HTTP)


def parse_sse(data: str) -> tuple[list[str], dict[str, Any], int, int]:
    images: list[str] = []
    completed_metadata: dict[str, Any] = {}
    parse_errors = 0
    events = 0
    failure_message: str | None = None

    for line in data.splitlines():
        if not line.startswith("data: "):
            continue
        s = line[6:].strip()
        if not s or s == "[DONE]":
            continue
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        events += 1
        if events > MAX_SSE_EVENTS:
            raise UserFacingError("Codex image response exceeded 512 SSE event limit", EXIT_API_FAILURE)
        t = ev.get("type")
        if t in ("response.failed", "error"):
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            failure_message = err.get("message") or ev.get("message") or err.get("code") or "Codex image generation failed"
        if t == "response.completed" and isinstance(ev.get("response"), dict):
            response = ev["response"]
            completed_metadata = {
                "usage": response.get("usage"),
                "tool_usage": response.get("tool_usage"),
            }
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        if t == "response.output_item.done" and item.get("type") == "image_generation_call" and item.get("result"):
            images.append(item["result"])
        response = ev.get("response") if isinstance(ev.get("response"), dict) else {}
        for out in response.get("output") or []:
            if isinstance(out, dict) and out.get("type") == "image_generation_call" and out.get("result"):
                images.append(out["result"])

    if failure_message:
        raise UserFacingError(f"API failure event: {redact(failure_message)}", EXIT_API_FAILURE)
    seen: set[str] = set()
    unique_images: list[str] = []
    for img in images:
        if img not in seen:
            seen.add(img)
            unique_images.append(img)
    return unique_images, completed_metadata, events, parse_errors


def decode_image(payload: str) -> bytes:
    if len(payload) > MAX_IMAGE_BASE64_CHARS:
        raise UserFacingError("Codex image result exceeded 64MB base64 limit", EXIT_DECODE_OR_WRITE)
    try:
        return base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise UserFacingError(f"Image result was not valid base64: {exc}", EXIT_DECODE_OR_WRITE)


def validate_image_magic(image: bytes, fmt: str) -> None:
    ok = False
    if fmt == "png":
        ok = image.startswith(b"\x89PNG\r\n\x1a\n")
    elif fmt == "jpeg":
        ok = image.startswith(b"\xff\xd8")
    elif fmt == "webp":
        ok = len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP"
    if not ok:
        raise UserFacingError(f"Decoded image does not look like requested format: {fmt}", EXIT_DECODE_OR_WRITE)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    try:
        token, account_id, auth_source = load_auth(args)
        if args.check_auth:
            print(f"Auth OK ({auth_source}; account_id={'present' if account_id else 'absent'})")
            return 0

        prompt_path, output_path, prompt = validate_local_inputs(args)
        req = build_request(args, prompt, token, account_id)
        started = time.time()
        status, content_type, data = read_http_response(req, args.timeout)
        elapsed = time.time() - started
        images, api_metadata, event_count, parse_errors = parse_sse(data)
        if not images:
            raise UserFacingError("NO_IMAGE_FOUND: Codex response did not contain an image_generation_call result", EXIT_NO_IMAGE)
        image = decode_image(images[0])
        validate_image_magic(image, args.format)
        try:
            output_path.write_bytes(image)
        except OSError as exc:
            raise UserFacingError(f"Could not write output image: {output_path}: {exc}", EXIT_DECODE_OR_WRITE)

        sse_path = None
        if args.save_sse:
            sse_path = output_path.with_suffix(output_path.suffix + ".sse")
            try:
                sse_path.write_text(data)
            except OSError as exc:
                raise UserFacingError(f"Could not write SSE debug file: {sse_path}: {exc}", EXIT_DECODE_OR_WRITE)

        metadata = {
            "output": str(output_path),
            "image_bytes": len(image),
            "size": args.size,
            "quality": args.quality,
            "format": args.format,
            "responses_model": args.responses_model,
            "image_model": args.image_model,
            "http_status": status,
            "content_type": content_type,
            "sse_bytes": len(data.encode("utf-8")),
            "sse_events": event_count,
            "sse_parse_errors": parse_errors,
            "elapsed_seconds": round(elapsed, 3),
            "usage": api_metadata.get("usage"),
            "tool_usage": api_metadata.get("tool_usage"),
        }
        if sse_path:
            metadata["sse_path"] = str(sse_path)
        if args.metadata:
            write_json(Path(args.metadata).expanduser(), metadata)

        print("HTTP", status, "content-type", content_type)
        print("IMAGE_PATH", output_path)
        print("IMAGE_BYTES", len(image))
        print("SIZE", args.size)
        print("QUALITY", args.quality)
        print("FORMAT", args.format)
        if args.verbose:
            print("SSE_BYTES", metadata["sse_bytes"])
            print("SSE_EVENTS", event_count)
            print("SSE_PARSE_ERRORS", parse_errors)
            if args.metadata:
                print("METADATA_PATH", Path(args.metadata).expanduser())
            if sse_path:
                print("SSE_PATH", sse_path)
        return 0
    except UserFacingError as exc:
        eprint(redact(str(exc)))
        return exc.exit_code
    except KeyboardInterrupt:
        eprint("Interrupted")
        return 130
    except Exception as exc:  # Last-resort safety net; keep secrets redacted.
        eprint("Unexpected error: " + redact(repr(exc)))
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
