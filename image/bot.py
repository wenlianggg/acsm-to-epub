import asyncio
import os
import subprocess
import logging
import shutil
import hashlib
import base64
import secrets
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


# ---- Paths & binaries ----
CREDENTIALS_DIR = "/home/libgourou/credentials"
INPUT_DIR = "/home/libgourou/input"
OUTPUT_DIR = "/home/libgourou/output"

ACSM_DOWNLOADER = "/usr/local/bin/acsmdownloader"
ADEPT_ACTIVATE = "/usr/local/bin/adept_activate"
ADEPT_REMOVE = "/usr/local/bin/adept_remove"
KEPUBIFY = "/home/libgourou/kepubify-linux-64bit"

# ---- Limits ----
ACSM_MAX_BYTES = 1 * 1024 * 1024
EPUB_MAX_BYTES = 200 * 1024 * 1024
USER_INPUT_QUOTA_BYTES = 100 * 1024 * 1024


# ---- Env bootstrap ----
_repo_root = Path(__file__).resolve().parents[1]
_env_path = _repo_root / ".env"
load_dotenv(dotenv_path=_env_path)


def _ensure_and_set_auth_key(env_path: Path):
    """Generate and persist AUTH_KEY_BASE64 in .env if missing."""
    if os.getenv("AUTH_KEY_BASE64"):
        return
    key = secrets.token_bytes(32)
    key_b64 = base64.urlsafe_b64encode(key).decode()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with env_path.open("a", encoding="utf-8") as f:
        f.write("\n# Auto-generated AUTH_KEY_BASE64\n")
        f.write(f"AUTH_KEY_BASE64={key_b64}\n")
    os.environ["AUTH_KEY_BASE64"] = key_b64


_ensure_and_set_auth_key(_env_path)


# ---- Token crypto ----
def _get_auth_key() -> bytes:
    key_b64 = os.getenv("AUTH_KEY_BASE64")
    if not key_b64:
        raise RuntimeError("AUTH_KEY_BASE64 env var is required for authorization tokens")
    try:
        key = base64.urlsafe_b64decode(key_b64)
    except Exception as exc:
        raise RuntimeError("AUTH_KEY_BASE64 must be valid base64") from exc
    if len(key) not in (16, 24, 32):
        raise RuntimeError("AUTH_KEY_BASE64 must decode to 16/24/32 bytes")
    return key


def encrypt_auth_token(user_id: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_get_auth_key()).encrypt(nonce, f"bookbot:{user_id}".encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_auth_token(token_b64: str) -> str:
    try:
        data = base64.urlsafe_b64decode(token_b64.encode())
    except Exception as exc:
        raise ValueError("invalid base64 token") from exc
    if len(data) < 13:
        raise ValueError("invalid token")
    nonce, ct = data[:12], data[12:]
    return AESGCM(_get_auth_key()).decrypt(nonce, ct, None).decode()


# ---- User state on disk ----
def _user_creds_dir(user_id) -> str:
    return os.path.join(CREDENTIALS_DIR, str(user_id))


def _tokendecoded_path(user_id) -> str:
    return os.path.join(_user_creds_dir(user_id), ".tokendecoded", "payload.txt")


def _write_tokendecoded(user_id, payload: str):
    p = _tokendecoded_path(user_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(payload)


def _has_tokendecoded(user_id) -> bool:
    p = _tokendecoded_path(user_id)
    return os.path.exists(p) and os.path.getsize(p) > 0


def _is_admin(user_id: int) -> bool:
    admin_id = os.getenv("ADMIN_USER_ID", "")
    return bool(admin_id) and str(user_id) == admin_id


# ---- Generic helpers ----
def _short_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:10]


def _directory_size(directory: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(directory):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total


def _format_size(num_bytes: int) -> str:
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


class StatusMessage:
    """A single Telegram message that gets edited as the pipeline progresses."""

    def __init__(self, message):
        self._message = message
        self._lines = [message.text or ""]

    @classmethod
    async def send(cls, update: Update, text: str) -> "StatusMessage":
        assert update.message is not None
        msg = await update.message.reply_text(text)
        return cls(msg)

    async def append(self, line: str):
        self._lines.append(line)
        try:
            await self._message.edit_text("\n\n".join(self._lines))
        except Exception as exc:
            logging.warning("Failed to edit status message: %s", exc)


async def _run_or_report(status: StatusMessage, args: list, error_msg: str):
    """Run a subprocess; on failure, update the status message and re-raise."""
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        await status.append(error_msg)
        await status.append(f"Error: {e.stdout} {e.stderr}")
        raise


# ---- Pipeline steps ----
async def download_acsm_content(status: StatusMessage, adept_path, acsm_file, work_dir) -> str:
    await _run_or_report(
        status,
        [ACSM_DOWNLOADER, "-f", acsm_file, "-O", work_dir, "-D", adept_path],
        "Error downloading ACSM content.",
    )
    file_name = os.listdir(work_dir)[0]
    size = _format_size(os.path.getsize(os.path.join(work_dir, file_name)))
    await status.append(f"Downloaded protected '{file_name}' ({size}) from content provider.")
    return file_name


async def adept_remove(status: StatusMessage, file_name, adept_path, work_dir):
    await _run_or_report(
        status,
        [ADEPT_REMOVE, "-f", os.path.join(work_dir, file_name), "-D", adept_path],
        "Error running dedrm.",
    )
    await status.append(f"Removed Adept DRM from '{file_name}'!")


async def kepubify(status: StatusMessage, file_name, output_path, work_dir) -> str:
    kepub_file = os.path.join(output_path, file_name).replace(".epub", ".kepub.epub")
    await _run_or_report(
        status,
        [KEPUBIFY, os.path.join(work_dir, file_name), "-o", kepub_file],
        "Error converting to KEPUB.",
    )
    await status.append(f"Converted '{file_name}' to kepub with kepubify!")
    return kepub_file


UPLOAD_TIMEOUT = 600
UPLOAD_MAX_RETRIES = 3


async def _send_document_with_retry(context, chat_id, file_path: str):
    last_err: Optional[Exception] = None
    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        try:
            with open(file_path, "rb") as f:
                await context.bot.send_document(
                    chat_id, f,
                    write_timeout=UPLOAD_TIMEOUT, read_timeout=UPLOAD_TIMEOUT,
                )
            return
        except (NetworkError, TimedOut) as e:
            last_err = e
            logging.warning("send_document attempt %d/%d failed: %s", attempt, UPLOAD_MAX_RETRIES, e)
            if attempt < UPLOAD_MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)
    assert last_err is not None
    raise last_err


async def send_files(update, context, file_name, output_path, kepub_file: Optional[str], send_original: bool = True):
    assert update.message is not None

    if send_original:
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "FILE"
        await update.message.reply_text(f"Sending you the {ext} file now!")
        await _send_document_with_retry(context, update.message.chat_id, os.path.join(output_path, file_name))

    if kepub_file:
        await update.message.reply_text("Sending you the kepub file now!")
        await _send_document_with_retry(context, update.message.chat_id, kepub_file)


# ---- Handlers ----
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /generate <name>")
        return
    name = context.args[0]
    token = encrypt_auth_token(name)
    await update.message.reply_text(f"Token for '{name}':\n`{token}`", parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    args = context.args

    if args:
        try:
            plaintext = decrypt_auth_token(args[0])
        except Exception:
            await update.message.reply_text("Invalid authorization token.")
            return
        if not plaintext.startswith("bookbot:"):
            await update.message.reply_text("Invalid authorization token")
            return
        payload = plaintext.split(":", 1)[1].strip()
        if not payload:
            await update.message.reply_text("Invalid token payload.")
            return
        _write_tokendecoded(user_id, payload)
        await update.message.reply_text("Authorization successful and payload saved. You no longer need to provide the token.")
    else:
        if not _has_tokendecoded(user_id):
            await update.message.reply_text(
                "Please provide your authorization token when using /start, e.g. `/start <token>`"
            )
            return
        await update.message.reply_text("Using existing saved credentials.")

    user_directory = _user_creds_dir(user_id)
    if os.path.exists(user_directory):
        await update.message.reply_text(f"Welcome back! Using existing directory for user {user_id}.")
        return

    try:
        subprocess.run(
            [ADEPT_ACTIVATE, "-a", "-O", user_directory],
            capture_output=True, text=True, check=True,
        )
        await update.message.reply_text("Created new set of credentials for you.")
        logging.info("Created new set of credentials for user %s.", user_id)
    except subprocess.CalledProcessError as e:
        await update.message.reply_text("Error running command.")
        raise e


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    uploaded = _directory_size(os.path.join(INPUT_DIR, str(user_id)))
    processed = _directory_size(os.path.join(OUTPUT_DIR, str(user_id)))
    await update.message.reply_text(
        f"To date, you ({user_id}) have uploaded {_format_size(uploaded)} "
        f"and processed {_format_size(processed)} of books!"
    )


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id

    if not _has_tokendecoded(user_id):
        await update.message.reply_text("You must authenticate first with `/start <token>` before uploading files.")
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("Sorry, I couldn't process the file.")
        return

    file_name = os.path.basename(document.file_name or "")
    lower_name = file_name.lower()
    if not file_name or not lower_name.endswith((".acsm", ".epub")):
        await update.message.reply_text("Please upload an .acsm or .epub file.")
        return

    is_acsm = lower_name.endswith(".acsm")
    size_limit = ACSM_MAX_BYTES if is_acsm else EPUB_MAX_BYTES
    if (document.file_size or 0) > size_limit:
        size_limit_mb = size_limit // (1024 * 1024)
        kind = "acsm" if is_acsm else "epub"
        await update.message.reply_text(f"The file is too large. Max {size_limit_mb}MB for .{kind} uploads.")
        return

    short_hash = _short_hash(document.file_id)
    input_path = os.path.join(INPUT_DIR, str(user_id))
    output_path = os.path.join(OUTPUT_DIR, str(user_id), short_hash)
    adept_path = _user_creds_dir(user_id)
    os.makedirs(input_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)

    if _directory_size(input_path) > USER_INPUT_QUOTA_BYTES:
        await update.message.reply_text("Your directory is too large. Please delete some files and try again.")
        return

    file_path = os.path.join(input_path, file_name)
    file = await context.bot.get_file(document.file_id)
    await file.download_to_drive(file_path)
    status = await StatusMessage.send(update, f"Starting to process '{file_name}'!")

    work_dir = f"/tmp/{file_path}_epub"
    os.makedirs(work_dir, exist_ok=True)

    if is_acsm:
        file_name = await download_acsm_content(status, adept_path, file_path, work_dir)
        await adept_remove(status, file_name, adept_path, work_dir)
    else:
        shutil.copy(file_path, os.path.join(work_dir, file_name))

    shutil.copy(os.path.join(work_dir, file_name), output_path)

    kepub_file = None
    if file_name.lower().endswith(".epub"):
        kepub_file = await kepubify(status, file_name, output_path, work_dir)
    await status.append(f"Finished processing '{file_name}'!")
    await send_files(update, context, file_name, output_path, kepub_file, send_original=is_acsm)


async def error_handler(_update, context):
    err = context.error
    if isinstance(err, subprocess.CalledProcessError):
        logging.warning("Pipeline subprocess failed (already reported to user): %s", err)
    elif isinstance(err, (NetworkError, TimedOut)):
        logging.warning("Telegram network error: %s", err)
    else:
        logging.error("Unhandled exception", exc_info=err)


def main():
    token = os.environ["BOT_TOKEN"]
    application = (
        Application.builder()
        .token(token)
        .base_url("http://telegram_bot_api:8081/bot")
        .base_file_url("http://telegram_bot_api:8081/file/bot")
        .local_mode(True)
        .connect_timeout(30)
        .read_timeout(120)
        .write_timeout(600)
        .pool_timeout(30)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))
    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
