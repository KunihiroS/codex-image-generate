# codex-image-generate

CLI helper for generating images via Codex/ChatGPT OAuth login using gpt-image-2.

Reads a ChatGPT OAuth access token from `~/.codex/auth.json` (created by `codex login`) and calls the Codex Responses backend with the `image_generation` tool. No separate OpenAI API key or FAL key is required.

## Disclaimer

This tool uses an internal Codex/ChatGPT backend endpoint and is not affiliated with or endorsed by OpenAI.

Compliance with OpenAI's terms of service and usage policies is your own responsibility to verify. The author takes no responsibility for any consequences, including account suspension or unintended quota consumption.

## What it does

- Reads a ChatGPT OAuth access token from `~/.codex/auth.json` by default.
- Calls `https://chatgpt.com/backend-api/codex/responses`.
- Uses `gpt-5.5` as the default Responses model.
- Uses `gpt-image-2` as the image generation tool model.
- Extracts the generated base64 image from SSE events.
- Saves the image and optional non-secret metadata.

## Important notes

This tool depends on Codex/ChatGPT backend behavior and may break if the backend changes.

Do not commit or share:

- `~/.codex/auth.json`
- access tokens, refresh tokens, id tokens
- account IDs
- raw SSE files unless intentionally saved for local debugging

Image generation may consume Codex/ChatGPT quota or subscription allowance.

## Requirements

- Python 3.10+
- Codex CLI installed and authenticated with ChatGPT (`codex login`)

Verify login:

```bash
codex login status
python3 scripts/codex_image_generate.py --check-auth
```

Expected output:

```
Logged in using ChatGPT
Auth OK (...; account_id=present)
```

## Basic usage

```bash
# Create a prompt file
printf 'A clean editorial illustration of a red circle on a white background. No text.\n' > /tmp/prompt.txt

# Generate an image
python3 scripts/codex_image_generate.py \
  --prompt /tmp/prompt.txt \
  --output outputs/test.png

# With metadata and verbose output
python3 scripts/codex_image_generate.py \
  --prompt examples/magazine_article_prompt.txt \
  --output outputs/magazine.png \
  --metadata outputs/magazine.metadata.json \
  --verbose

# Overwrite existing output
python3 scripts/codex_image_generate.py \
  --prompt examples/magazine_article_prompt.txt \
  --output outputs/magazine.png \
  --overwrite

# Save raw SSE for debugging
python3 scripts/codex_image_generate.py \
  --prompt examples/magazine_article_prompt.txt \
  --output outputs/debug.png \
  --save-sse \
  --verbose
```

## Defaults

| Option | Default |
|---|---|
| size | `2048x1152` |
| quality | `high` |
| format | `png` |
| responses model | `gpt-5.5` |
| image model | `gpt-image-2` |
| timeout | `300` seconds |
| output | `codex-image-output.png` in current directory |

## Options

```
--prompt PATH
--output PATH
--size {1024x1024,1024x1536,1536x1024,2048x1152,2048x2048,2160x3840,3840x2160}
--quality {low,medium,high}
--format {jpeg,png,webp}
--auth-file PATH
--base-url URL
--responses-model MODEL
--image-model MODEL
--timeout SECONDS
--overwrite
--save-sse
--metadata PATH
--verbose
--check-auth
```

## Auth resolution order

1. `CODEX_ACCESS_TOKEN` environment variable (and optional `CODEX_ACCOUNT_ID`)
2. `--auth-file` CLI option
3. `CODEX_AUTH_FILE` environment variable
4. `~/.codex/auth.json`

The script does not refresh tokens automatically. If auth fails, run:

```bash
codex login status
codex login
```

## Error handling

- Missing or empty prompt file
- Existing output without `--overwrite`
- Missing or invalid auth file / access token
- HTTP 401 / 403 / 429 / 5xx with human-readable hints
- SSE response size and event count limits
- Invalid base64 result
- PNG / JPEG / WebP magic-byte mismatch
- API failure events in SSE stream

## Tests

The included tests do not send generation requests and do not consume quota.

```bash
python3 tests/test_codex_image_generate.py
```

## Repository layout

```
.
├── README.md
├── SKILL.md
└── scripts/
    └── codex_image_generate.py
```
