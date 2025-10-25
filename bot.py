import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes,
    PicklePersistence # NEW: To save user data
)
from telegram.constants import ChatAction
from openai import OpenAI # We use this to create clients on-the-fly

# --- Initial Setup ---

# 1. Load environment variables
load_dotenv()

# 2. Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 3. Read ONLY the Telegram token. The OpenRouter key will be provided by the user.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("Telegram Token not found. Make sure it's in the .env file")

# 4. Set the model and the SYSTEM PROMPT
MODELLO_IA = "mistralai/mistral-small-3.2-24b-instruct:free" 

# -----------------------------------------------------------------
# NEW: Set your custom instructions here!
# Change this string to give your bot a personality or specific instructions.
SYSTEM_PROMPT = "You are an AI assistant named “Will,” and you will always be ready to respond with short, accurate answers or long, detailed ones depending on the context. The first thing you'll do in a chat is understanding the context and respond with the same language the user is speaking. If you're asked, you'll answer that your creator is lollo21, an italian indie developer."
# -----------------------------------------------------------------


# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the /start command is issued."""
    user_name = update.effective_user.first_name
    await update.message.reply_html(
        f"Hi <b>{user_name}</b>, I'm <b>Will</b>, an AI assistant created by lollo21! 👋\n\n"
        "To use me, you must first provide your OpenRouter API key.\n\n"
        "Use the command:\n`/setkey YOUR_API_KEY`\n\n"
        "Your key will only be used to process your requests. "
        "For security, the message containing your key will be deleted immediately.",
        parse_mode='Markdown'
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NEW: Saves the user's API key."""
    user_id = update.effective_user.id
    
    # context.args contains the words after the /setkey command
    if not context.args:
        await update.message.reply_text("Error: You must provide a key.\nUsage: /setkey <your_api_key>")
        return

    user_key = context.args[0]

    # Save the key in the user's persistent data
    # context.user_data is a dictionary unique to each user
    context.user_data['api_key'] = user_key
    
    logger.info(f"API Key set for user {user_id}")
    
    # For security, try to delete the user's original message
    try:
        await update.message.delete()
        await update.message.reply_text(
            "✅ OpenRouter API key saved successfully! "
            "Your original message has been deleted for security.\n\n"
            "Now you can start chatting."
        )
    except Exception as e:
        logger.warning(f"Could not delete the key message: {e}")
        await update.message.reply_text(
            "✅ OpenRouter API key saved! "
            "(I couldn't delete your original message, I recommend deleting it manually)."
        )

async def my_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NEW: Checks if a key is set."""
    if 'api_key' in context.user_data:
        # Show only part of the key for security
        key_preview = context.user_data['api_key'][:4] + "..." + context.user_data['api_key'][-4:]
        await update.message.reply_text(f"You have an API key set: `{key_preview}`", parse_mode='Markdown')
    else:
        await update.message.reply_text("You haven't set an API key yet. Use /setkey <your_key>.")

async def del_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NEW: Removes the user's API key."""
    if 'api_key' in context.user_data:
        del context.user_data['api_key']
        await update.message.reply_text("🗑️ Your API key has been removed.")
    else:
        await update.message.reply_text("You don't have an API key to remove.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages and forwards the request to OpenRouter."""
    
    # 1. NEW: Check if the user has set an API key
    if 'api_key' not in context.user_data:
        await update.message.reply_text(
            "You must set your OpenRouter API key first. "
            "Use the command: /setkey <your_api_key>"
        )
        return

    # If we're here, the user has a key.
    user_text = update.message.text
    chat_id = update.message.chat_id
    user_key = context.user_data['api_key'] # Get the specific user's key

    # 2. Send "typing..." action
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        # 3. NEW: Create an OpenRouter client "on-the-fly" USING THE USER'S KEY
        client_user = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=user_key,
        )

        # 4. Call the OpenRouter API
        completion = client_user.chat.completions.create(
            model=MODELLO_IA,
            messages=[
                # NEW: Use the variable for the custom prompt
                {"role": "system", "content": SYSTEM_PROMPT}, 
                {"role": "user", "content": user_text},
            ],
        )

        # 5. Extract and send the response
        response_text = completion.choices[0].message.content
        await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Error calling OpenRouter for user {update.effective_user.id}: {e}")
        # Handle a common error (wrong API key)
        if "Incorrect API key" in str(e): # This string comes from the API, do NOT translate
             await update.message.reply_text("😔 Your OpenRouter API key seems to be incorrect or invalid. Please try again with /setkey")
        else:
            await update.message.reply_text("😔 Sorry, an error occurred. Please try again later.")


# --- Main Function to Start the Bot ---

def main() -> None:
    """Start the bot and listen for messages."""
    
    # NEW: Configure persistence
    # This will create a file "bot_persistence.pickle" to save context.user_data
    persistence = PicklePersistence(filepath="bot_persistence.pickle")

    # NEW: Add 'persistence' to the builder
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .build()
    )

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    
    # NEW: Add new handlers for key management
    application.add_handler(CommandHandler("setkey", set_key))
    application.add_handler(CommandHandler("mykey", my_key))
    application.add_handler(CommandHandler("delkey", del_key))

    # Add the handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    logger.info("Starting bot (Version 2.0)... Press CTRL+C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()
