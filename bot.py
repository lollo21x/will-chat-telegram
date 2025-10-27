import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    PicklePersistence,
    filters,  # Corretta importazione
)
from telegram.constants import ChatAction
from openai import OpenAI
from fastapi import FastAPI, Request
import uvicorn

# --- 1. Initial Setup ---
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Telegram Token not found. Make sure it's in your environment variables.")

MODELLO_IA = "mistralai/mistral-small-3.2-24b-instruct:free"
SYSTEM_PROMPT = (
    "You are an AI assistant named 'Will', and you will always be ready to respond "
    "with short, accurate answers or long, detailed ones depending on the context. "
    "The first thing you'll do in a chat is understanding the context and respond "
    "with the same language the user is speaking. If you're asked, you'll answer "
    "that your creator is lollo21, an italian indie developer."
)

# Porta fornita dall'ambiente di hosting (Railway, Render, etc.)
PORT = int(os.environ.get("PORT", 8080))
# URL pubblico del tuo servizio (DEVI IMPOSTARLO NELLE VARIABILI D'AMBIENTE)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")


# --- 3. Telegram Bot Setup ---
persistence = PicklePersistence(filepath="bot_persistence.pickle")
application = (
    Application.builder()
    .token(TELEGRAM_TOKEN)
    .persistence(persistence)
    .build()
)

# --- 4. Telegram Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce il comando /start (con formattazione HTML corretta)"""
    user_name = update.effective_user.first_name
    await update.message.reply_html(
        f"Hi <b>{user_name}</b>, I'm <b>Will</b>, an AI assistant created by lollo21! 👋\n\n"
        "To use me, you must first provide your OpenRouter API key.\n\n"
        "Use the command:\n<code>/setkey YOUR_API_KEY</code>\n\n"
        "Your key will only be used to process your requests. "
        "For security, the message containing your key will be deleted immediately."
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Salva la chiave API dell'utente"""
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Error: You must provide a key.\nUsage: /setkey <your_api_key>")
        return

    user_key = context.args[0]
    context.user_data["api_key"] = user_key
    logger.info(f"API Key set for user {user_id}")

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
            "(I couldn't delete your original message, please delete it manually)."
        )

async def my_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra uno snippet della chiave salvata"""
    if "api_key" in context.user_data:
        key_preview = context.user_data["api_key"][:4] + "..." + context.user_data["api_key"][-4:]
        await update.message.reply_text(f"You have an API key set: `{key_preview}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("You haven't set an API key yet. Use /setkey <your_key>.")

async def del_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rimuove la chiave API"""
    if "api_key" in context.user_data:
        del context.user_data["api_key"]
        await update.message.reply_text("🗑️ Your API key has been removed.")
    else:
        await update.message.reply_text("You don't have an API key to remove.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce tutti i messaggi di testo"""
    if "api_key" not in context.user_data:
        await update.message.reply_text(
            "You must set your OpenRouter API key first. Use the command: /setkey <your_api_key>"
        )
        return

    # Evita di processare i messaggi se non sono di testo
    if not update.message or not update.message.text:
        return
        
    user_text = update.message.text
    chat_id = update.message.chat_id
    user_key = context.user_data["api_key"]

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        client_user = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=user_key)
        completion = client_user.chat.completions.create(
            model=MODELLO_IA,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        )

        response_text = completion.choices[0].message.content
        await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Error calling OpenRouter for user {update.effective_user.id}: {e}")
        # Corretta indentazione del blocco exception
        if "Incorrect API key" in str(e):
            await update.message.reply_text(
                "😔 Your OpenRouter API key seems to be incorrect or invalid. Please try again with /setkey"
            )
        else:
            await update.message.reply_text("😔 Sorry, an error occurred. Please try again later.")

# --- 5. Registra gli Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("setkey", set_key))
application.add_handler(CommandHandler("mykey", my_key))
application.add_handler(CommandHandler("delkey", del_key))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


# --- 6. FastAPI Server Setup ---
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """All'avvio, inizializza il bot e imposta il webhook."""
    await application.initialize()
    
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL not set. Bot will not be able to receive messages.")
        return

    # Imposta il webhook
    webhook_path = f"/{TELEGRAM_TOKEN}"
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
    logger.info(f"Webhook set successfully to {WEBHOOK_URL}{webhook_path}")
    
    # Avvia l'applicazione PTB (necessario per far funzionare i job, etc.)
    await application.start()

@app.on_event("shutdown")
async def shutdown_event():
    """Alla chiusura, ferma il bot e rimuovi il webhook."""
    logger.info("Shutting down bot...")
    await application.stop()
    await application.bot.delete_webhook()
    await application.shutdown()
    logger.info("Bot shutdown complete.")

@app.post("/{token}")
async def telegram_webhook(token: str, request: Request):
    """Gestisce gli update in arrivo da Telegram."""
    if token != TELEGRAM_TOKEN:
        return {"status": "error", "message": "Invalid token"}

    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return {"status": "error"}

@app.get("/")
async def health_check():
    """Un semplice endpoint per controllare che il server sia vivo."""
    return {"status": "ok", "bot_name": "Will AI Bot"}

# --- 7. Run Server (solo per test locale) ---
# Quando deployato, Railway/Render useranno un comando Uvicorn diverso.
if __name__ == "__main__":
    logger.info(f"Starting server locally on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
