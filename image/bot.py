import os
import subprocess
import logging
import shutil
import hashlib
import base64
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Load .env from repository root and ensure AUTH_KEY_BASE64 exists
_repo_root = Path(__file__).resolve().parents[1]
_env_path = _repo_root / ".env"
load_dotenv(dotenv_path=_env_path)

def _ensure_and_set_auth_key(env_path: Path):
    """Generate and persist AUTH_KEY_BASE64 in .env if missing."""
    if os.getenv("AUTH_KEY_BASE64"):
        return
    key = secrets.token_bytes(32)
    key_b64 = base64.urlsafe_b64encode(key).decode()
    # append to .env
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with env_path.open("a", encoding="utf-8") as f:
        f.write("\n# Auto-generated AUTH_KEY_BASE64\n")
        f.write(f"AUTH_KEY_BASE64={key_b64}\n")
    os.environ["AUTH_KEY_BASE64"] = key_b64

_ensure_and_set_auth_key(_env_path) 

def _get_auth_key() -> bytes:
    """Return the raw AES key from the environment variable AUTH_KEY_BASE64.
    The env var must be base64-urlsafe encoded and decode to 16/24/32 bytes.
    """
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


def decrypt_auth_token(token_b64: str) -> str:
    """Decrypt a token produced by encrypt_auth_token and return the plaintext string."""
    try:
        data = base64.urlsafe_b64decode(token_b64.encode())
    except Exception as exc:
        raise ValueError("invalid base64 token") from exc
    if len(data) < 13:
        raise ValueError("invalid token")
    nonce = data[:12]
    ct = data[12:]
    aesgcm = AESGCM(_get_auth_key())
    pt = aesgcm.decrypt(nonce, ct, None)
    return pt.decode()


def encrypt_auth_token(user_id: str) -> str:
    """Create a base64-urlsafe token for the given user id. Returns a short base64 string."""
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(_get_auth_key())
    pt = f"bookbot:{user_id}".encode()
    ct = aesgcm.encrypt(nonce, pt, None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def _tokendecoded_dir(user_id: str) -> str:
    return os.path.join("/home/libgourou/credentials", str(user_id), ".tokendecoded")


def _write_tokendecoded(user_id: str, payload: str):
    d = _tokendecoded_dir(user_id)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "payload.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(payload)


def _has_tokendecoded(user_id: str) -> bool:
    p = os.path.join(_tokendecoded_dir(user_id), "payload.txt")
    return os.path.exists(p) and os.path.getsize(p) > 0


def _is_admin(user_id: int) -> bool:
    admin_id = os.getenv("ADMIN_USER_ID", "")
    return bool(admin_id) and str(user_id) == admin_id


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
    user_id = update.effective_user.id
    args = context.args

    if not args or len(args) < 1:
        if not _has_tokendecoded(user_id):
            await update.message.reply_text(
                "Please provide your authorization token when using /start, e.g. `/start <token>`"
            )
            return
        await update.message.reply_text("Using existing saved credentials.")
    else:
        token = args[0]
        try:
            plaintext = decrypt_auth_token(token)
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

        # Save payload into credentials/.tokendecoded
        _write_tokendecoded(user_id, payload)
        await update.message.reply_text("Authorization successful and payload saved. You no longer need to provide the token.")

    user_directory = f"/home/libgourou/credentials/{user_id}"

    # Create a directory for the user if it doesn't exist
    if os.path.exists(user_directory):
        await update.message.reply_text(f"Welcome back! Using existing directory for user {user_id}.")
    else:
        # Run a shell command in the user's directory (example: list files)
        command = ["/usr/local/bin/adept_activate", "-a", "-O", user_directory]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
            await update.message.reply_text(f"Created new set of credentials for you.")
            logging.info(f"Created new set of credentials for user {user_id}.")
        except subprocess.CalledProcessError as e:
            await update.message.reply_text(f"Error running command.")
            raise e


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Ensure the user has completed token-based auth at least once
    if not _has_tokendecoded(user_id):
        await update.message.reply_text("You must authenticate first with `/start <token>` before uploading files.")
        return

    document = update.message.document
    
    if not document:
        await update.message.reply_text("Sorry, I couldn't process the file.")
        return
    
    file_id = document.file_id
    def generate_short_hash(file_id):
        return hashlib.sha256(file_id.encode()).hexdigest()[:10]

    short_hash = generate_short_hash(file_id)
    file_name = os.path.basename(document.file_name or "")

    if not file_name or not file_name.lower().endswith('.acsm'):
        await update.message.reply_text("Please upload a valid file.")
        return
    
    file_size = document.file_size
    if file_size > 1 * 1024 * 1024:
        await update.message.reply_text("The file is too large. Please upload a file smaller than 1MB.")
        return
    
    # Get the file from Telegram
    file = await context.bot.get_file(file_id)

    input_path = os.path.join("/home/libgourou/input", f"{user_id}")
    output_path = os.path.join("/home/libgourou/output", f"{user_id}", f"{short_hash}")
    adept_path = os.path.join("/home/libgourou/credentials", f"{user_id}")
    os.makedirs(input_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)

    # Ensure the total size of the user directory is less than 10MB
    def get_directory_size(directory):
        total_size = 0
        for dirpath, _, filenames in os.walk(directory):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size

    if get_directory_size(input_path) > 100 * 1024 * 1024:
        await update.message.reply_text("Your directory is too large. Please delete some files and try again.")
        return

    # Save the file locally
    file_path = os.path.join(input_path, file_name)
    await file.download_to_drive(file_path)
    await update.message.reply_text(f"Starting to process '{file_name}'!")

    acsm_file = file_path
    acsm_folder = f"/tmp/{acsm_file}_epub"
    os.makedirs(acsm_folder, exist_ok=True)

    file_name = await download_acsm_content(update, adept_path, acsm_file, acsm_folder)
    await adept_remove(update, file_name, adept_path, acsm_folder)
    shutil.copy(f"{acsm_folder}/{file_name}", output_path)
    kepub_file = await kepubify(update, file_name, output_path, acsm_folder)
    await send_files(update, context, file_name, output_path, kepub_file)


async def download_acsm_content(update, adept_path, acsm_file, acsm_folder):
    try:
        subprocess.run([
            '/usr/local/bin/acsmdownloader',
            '-f', acsm_file,
            '-O', acsm_folder,
            "-D", adept_path
        ], check=True, capture_output=True, text=True)
        file_name = os.listdir(acsm_folder)[0]
        await update.message.reply_text(
            f"""
            Downloaded '{file_name}' from content provider.
            """
        )
    except subprocess.CalledProcessError as e:
        await update.message.reply_text("Error downloading ACSM content.")
        await update.message.reply_text(f"Error: {e.stdout} {e.stderr}")
        raise e

    return file_name


async def adept_remove(update, file_name, adept_path, acsm_folder):
    try:
        subprocess.run([
            '/usr/local/bin/adept_remove',
            '-f', f"{acsm_folder}/{file_name}",
            "-D", adept_path,
        ], check=True)
        await update.message.reply_text(
            f"""
            Processed file '{file_name}'!
            """
        )
    except subprocess.CalledProcessError as e:
        await update.message.reply_text("Error running dedrm.")
        await update.message.reply_text(f"Error: {e.stdout} {e.stderr}")
        raise e


async def kepubify(update, file_name, output_path, acsm_folder) -> str:
    try:
        kepub_file = f"{output_path}/{file_name}".replace(".epub", ".kepub.epub")
        subprocess.run([
            '/home/libgourou/kepubify-linux-64bit',
            f"{acsm_folder}/{file_name}",
            "-o", kepub_file,
        ], check=True)
        await update.message.reply_text(
            f"""
            Converted '{file_name}' to KEPUB!
            """
        )
    except subprocess.CalledProcessError as e:
        await update.message.reply_text("Error converting to KEPUB.")
        await update.message.reply_text(f"Error: {e.stdout} {e.stderr}")
        raise e
    
    return kepub_file


async def send_files(update, context, file_name, output_path, kepub_file):
    await update.message.reply_text(f"Finished processing '{file_name}'!")
    
    # Semd the file back to the user
    with open(f"{output_path}/{file_name}", "rb") as f:
        await update.message.reply_text(f"Sending you the EPUB file now!")
        await context.bot.send_document(update.message.chat_id, f)

    with open(kepub_file, "rb") as f:
        await update.message.reply_text(f"Sending you the KEPUB file now!")
        await context.bot.send_document(update.message.chat_id, f)


def main():
    token = os.environ["BOT_TOKEN"]
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("generate", generate))

    # Message handlers for file uploads
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    # Start the bot
    application.run_polling()


if __name__ == "__main__":
    main()