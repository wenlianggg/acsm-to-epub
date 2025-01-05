import os
import subprocess
import logging
import shutil
import hashlib
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
    document = update.message.document
    
    if not document:
        await update.message.reply_text("Sorry, I couldn't process the file.")
        return
    
    file_id = document.file_id
    def generate_short_hash(file_id):
        return hashlib.sha256(file_id.encode()).hexdigest()[:10]

    short_hash = generate_short_hash(file_id)
    file_name = document.file_name

    if not file_name.lower().endswith('.acsm'):
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

    if get_directory_size(input_path) > 10 * 1024 * 1024:
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

    # Add a command handler for the /start command
    application.add_handler(CommandHandler("start", start))

    # Message handlers for file uploads
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))

    # Start the bot
    application.run_polling()


if __name__ == "__main__":
    main()