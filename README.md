# acsm-to-epub

Converts DRM-protected ACSM (Adobe Content Server Message) ebook files to standard EPUB and Kindle-compatible KEPUB formats. Runs as either a Telegram bot (interactive, multi-user) or a CLI batch processor.

## How it works

```
.acsm file → acsmdownloader → adept_remove (DRM strip) → kepubify → .epub + .kepub
```

The container is built on [`bcliang/docker-libgourou`](https://github.com/bcliang/docker-libgourou), which bundles the open-source [libgourou](https://forge.epita.fr/opds/libgourou) library and associated Adobe tools (`acsmdownloader`, `adept_activate`, `adept_remove`).

## Modes

| Mode | Use case |
|------|----------|
| **Telegram bot** | Interactive, multi-user — send ACSM files via chat and receive EPUB + KEPUB back |
| **CLI batch** | Drop ACSM files into `./input/`, run once, collect output |

## Prerequisites

- Docker and Docker Compose
- A Telegram bot token (bot mode only) — create one via [@BotFather](https://t.me/botfather)

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd acsm-to-epub
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
BOT_TOKEN=<your-telegram-bot-token>
AUTH_KEY_BASE64=<base64-encoded-32-byte-aes-key>
```

To generate `AUTH_KEY_BASE64` automatically:

```bash
pip install cryptography python-dotenv
python scripts/init_env.py
```

Run with `--force` to rotate the key later: `python scripts/init_env.py --force`

### 3. Build the Docker image

```bash
docker compose build
```

## Usage

### Telegram bot mode

**Start the bot:**

```bash
docker compose --profile bot up -d
```

**Generate an auth token for a user** (replace `<user_id>` with the Telegram user ID):

```bash
AUTH_KEY_BASE64=$(grep AUTH_KEY_BASE64 .env | cut -d= -f2) \
  python scripts/generate_auth_token.py <user_id>
```

Share the printed token with the user. They authenticate by sending the bot:

```
/start <token>
```

The bot will automatically create Adobe device credentials for them. They can then upload any `.acsm` file directly in the chat — the bot replies with both the `.epub` and `.kepub` files.

**Limits per user:**
- Max file size: 1 MB per ACSM file
- Max storage: 10 MB per user

### CLI batch mode

1. Place `.acsm` files in `./input/`
2. (Optional) Place existing Adobe credentials in `./creds/` — otherwise anonymous credentials are created automatically
3. Run:

```bash
docker compose --profile cli up
```

4. Collect output from `./output/` (EPUB) and `./output_kepub/` (KEPUB)

## Directory structure

```
acsm-to-epub/
├── image/
│   ├── Dockerfile            # Container image definition
│   ├── bot.py                # Telegram bot application
│   ├── dedrm_all.sh          # Batch: process all ACSM files in ./input/
│   ├── dedrm_one.sh          # Process a single ACSM file
│   ├── requirements.txt      # Python dependencies
│   └── kepubify-linux-64bit  # EPUB → KEPUB converter binary
├── scripts/
│   ├── init_env.py           # Generate AUTH_KEY_BASE64 and write to .env
│   └── generate_auth_token.py# Generate encrypted user auth tokens
├── input/                    # Drop ACSM files here (CLI mode)
├── output/                   # EPUB output (CLI mode)
├── output_kepub/             # KEPUB output (CLI mode)
├── creds/                    # Shared Adobe credentials (CLI mode)
├── usercreds/                # Per-user Adobe credentials (bot mode)
├── docker-compose.yaml
└── .env                      # BOT_TOKEN, AUTH_KEY_BASE64
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram Bot API token from @BotFather |
| `AUTH_KEY_BASE64` | Base64-encoded 32-byte AES-256 key used to sign user auth tokens |

## Security notes

- Auth tokens are AES-256-GCM encrypted and tied to a specific Telegram user ID — they cannot be reused by other users.
- Each bot user gets an isolated credentials directory and file quota.
- The `.env` file, `creds/`, `usercreds/`, and all ACSM/EPUB files are gitignored.
- ACSM conversion relies on libgourou, an open-source implementation of the Adobe DRM protocol. Use only for content you are legally permitted to access.
