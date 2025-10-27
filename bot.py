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
    filters,  # Assicurati che filters sia importato
)
from telegram.constants import ChatAction
from openai import OpenAI
from fastapi import FastAPI, Request  # Importa FastAPI
import uvicorn  # Importa Uvicorn

# --- 1. Setup Iniziale ---
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. Carica Variabili d'Ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN non trovato. Assicurati che sia nelle variabili d'ambiente.")

MODELLO_IA = "mistralai/mistral-small-3.2-24b-instruct:free"
SYSTEM_PROMPT = (
    "You are an AI assistant named 'Will', and you will always be ready to respond "
    "with short, accurate answers or long, detailed ones depending on the context. "
    "The first thing you'll do in a chat is understanding the context and respond "
    "with the same language the user is speaking. If you're asked, you'll answer "
    "that your creator is lollo21, an italian indie developer."
)

# --- 3. Setup App Telegram ---
# (Costruiamo l'applicazione qui, così FastAPI può usarla)
persistence = PicklePersistence(filepath="bot_persistence.pickle")
application = (
    Application.builder()
    .token(TELEGRAM_TOKEN)
    .persistence(persistence)
    .build()
)

# --- 4. Gestori Comandi Telegram (Il tuo codice, corretto) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    await update.message.reply_html(
        f"Hi <b>{user_name}</b>, I'm <b>Will</b>, an AI assistant created by lollo21! 👋\n\n"
        "To use me, you must first provide your OpenRouter API key.\n\n"
        "Use the command:\n<code>/setkey YOUR_API_KEY</code>\n\n"
        "Your key will only be used to process your requests. "
        "For security, the message containing your key will be deleted immediately."
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if "api_key" in context.user_data:
        key_preview = context.user_data["api_key"][:4] + "..." + context.user_data["api_key"][-4:]
        await update.message.reply_text(f"You have an API key set: `{key_preview}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("You haven't set an API key yet. Use /setkey <your_key>.")

async def del_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "api_key" in context.user_data:
        del context.user_data["api_key"]
        await update.message.reply_text("🗑️ Your API key has been removed.")
    else:
        await update.message.reply_text("You don't have an API key to remove.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "api_key" not in context.user_data:
        await update.message.reply_text(
            "You must set your OpenRouter API key first. Use the command: /setkey <your_api_key>"
        )
        return

    # Aggiunto controllo per assicurarsi che il messaggio sia di testo
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
        if "Incorrect API key" in str(e):
            await update.message.reply_text(
                "😔 Your OpenRouter API key seems to be incorrect or invalid. Please try again with /setkey"
            )
        else:
            await update.message.reply_text("😔 Sorry, an error occurred. Please try again later.")

# --- 5. Registra gli Handlers sull'App ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("setkey", set_key))
application.add_handler(CommandHandler("mykey", my_key))
application.add_handler(CommandHandler("delkey", del_key))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- 6. Setup Server FastAPI ---
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """All'avvio, inizializza il bot e avvia l'app PTB."""
    await application.initialize()  # Inizializza l'app
    await application.start()       # Avvia i processi in background (come i JobQueue, se usati)
    logger.info("✅ Bot application initialized and started.")

@app.on_event("shutdown")
async def shutdown_event():
    """Alla chiusura, ferma l'app PTB."""
    logger.info("Shutting down bot...")
    await application.stop()
    await application.shutdown()
    logger.info("🛑 Bot shutdown complete.")

@app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    """Questo è l'endpoint che riceve gli update da Telegram."""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return {"status": "error"}

@app.get("/")
async def health_check():
    """Endpoint per controllare se il server è attivo (utile per Render)."""
    return {"status": "ok", "bot_name": "Will AI Bot (Running)"}

# --- 7. Avvia il Server (se eseguito come script) ---
if __name__ == "__main__":
    # Render userà il 'Start Command', ma questo permette di eseguire `python bot.py`
    port = int(os.environ.get("PORT", 10000)) # Render usa la porta 10000
    logger.info(f"Starting server on host 0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
