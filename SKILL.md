---
name: codex-image-generation
description: Use this skill for any image generation task that relies on a local Codex or ChatGPT login — including gpt-image-2 via Codex subscription, ChatGPT OAuth image generation, and the codex_image_generate.py helper. Apply it even when the user only mentions wanting to use their Codex or ChatGPT account for image generation without naming the script explicitly. Do not substitute FAL-backed tools or direct OpenAI API key approaches when the user has a Codex/ChatGPT login available.
version: 0.1.0
---

# Codex Image Generation via ChatGPT OAuth

Use this skill when the user wants to generate images using a local Codex/ChatGPT login instead of a separate OpenAI API key, FAL key, or built-in `image_generate` backend.

This workflow is for personal/local use. Treat the route as backend-compatibility based, not a stable public API. It may break if Codex/ChatGPT changes its internals.

## Security rules

- Never print, summarize, save, or expose these values: `access_token`, `refresh_token`, `id_token`, `OPENAI_API_KEY`, `account_id`, `Authorization` header, or full `~/.codex/auth.json` contents.
- When inspecting auth files, only report structure or presence, never values.
- Before making an actual image generation request, mention that it may consume Codex/ChatGPT quota or subscription allowance unless the user has already clearly authorized the run.
- Prefer non-billing checks (`--help`, `--check-auth`, local tests) before live generation.

## Preconditions

The environment should have:

1. Python 3.
2. Codex CLI installed and authenticated with ChatGPT.
3. `codex login status` showing `Logged in using ChatGPT`.
4. A Codex auth file, normally `~/.codex/auth.json`, containing ChatGPT OAuth tokens.

The helper script does not need to run `codex exec`; it reads the Codex auth file or environment token and directly calls the Codex backend.

## Backend route

POST to:

`https://chatgpt.com/backend-api/codex/responses`

Request shape:

- main Responses model: default `gpt-5.5`
- tool: `image_generation`
- tool model: default `gpt-image-2`
- `tool_choice: {"type": "image_generation"}`
- `stream: true`
- `store: false`
- auth: `Authorization: Bearer <access_token>`
- if available: `ChatGPT-Account-Id: <account_id>`

Parse SSE events and extract base64 image data from `image_generation_call.result`, typically in `response.output_item.done` or completed `response.output`.

Note: Do not pass `gpt-image-2` as the main Codex model. It must be used exclusively as the `image_generation` tool model; passing it as the top-level model fails for ChatGPT accounts. Similarly, do not treat Hermes's built-in `image_generate` as equivalent — it uses an unrelated backend (FAL) and requires a separate `FAL_KEY`.

## Helper script convention

Preferred helper name:

`codex_image_generate.py`

Packaged helper path:

`scripts/codex_image_generate.py`

Recommended CLI:

```bash
python3 scripts/codex_image_generate.py \
  --prompt prompt.txt \
  --output out.png
```

Useful options:

```bash
--size 2048x1152              # default landscape
--quality high                # low|medium|high; default high
--format png                  # png|jpeg|webp; default png
--auth-file PATH              # override auth file
--base-url URL                # default https://chatgpt.com/backend-api/codex
--responses-model gpt-5.5
--image-model gpt-image-2
--timeout 300
--overwrite
--save-sse                   # save raw SSE only when debugging
--metadata out.metadata.json
--verbose
--check-auth                 # validate auth without sending generation request
```

Supported sizes:

- `1024x1024`
- `1536x1024`
- `1024x1536`
- `2048x2048`
- `2048x1152`
- `3840x2160`
- `2160x3840`

Only generate live images after user consent if quota consumption matters.

## Environment-independent auth resolution

Use this priority order:

1. `CODEX_ACCESS_TOKEN` and optional `CODEX_ACCOUNT_ID`.
2. `--auth-file` CLI option.
3. `CODEX_AUTH_FILE` environment variable.
4. `~/.codex/auth.json`.

Do not implement refresh-token handling in the helper. If a request returns 401 or auth looks stale, tell the user to run `codex login status` and then `codex login` if needed.

## Robust error handling to preserve

A distribution-ready helper should include:

- prompt file exists, is a file, and is not empty.
- output parent directory creation.
- default refusal to overwrite existing output unless `--overwrite` is passed.
- clear auth errors for missing/invalid auth file, missing `tokens`, or missing `access_token`.
- status-specific HTTP hints:
  - 401: token expired or login needed.
  - 403: account/entitlement/policy issue.
  - 429: rate limit or quota.
  - 5xx: backend temporary failure.
- strict redaction for token-like strings in all errors.
- SSE response size limit, e.g. 64MB.
- SSE event limit, e.g. 512 events.
- base64 result size limit, e.g. 64MB.
- `response.failed` and `error` SSE events treated as failure.
- image magic-byte validation for PNG/JPEG/WebP.
- optional metadata JSON with non-secret details only: output path, image bytes, size, quality, format, models, HTTP status, SSE bytes/events, elapsed seconds, usage/tool_usage if returned.
- raw SSE saved only with explicit `--save-sse`.

## Verification workflow

1. Confirm Codex is logged in without exposing secrets:

```bash
codex login status
python3 scripts/codex_image_generate.py --check-auth
```

2. Run non-billing/unit tests first if available.

3. For a live smoke test, use a small/low-cost prompt and low quality, and save to a disposable output path:

```bash
python3 scripts/codex_image_generate.py \
  --prompt /tmp/prompt.txt \
  --output /tmp/codex-image-smoke.png \
  --size 1536x1024 \
  --quality low \
  --metadata /tmp/codex-image-smoke.metadata.json \
  --verbose
```

4. Verify file type with `file <output>` and optionally vision-analyze the image.

