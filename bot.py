import os
import logging
import requests # Importato per le richieste HTTP
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    PicklePersistence,
    filters,
)
from telegram.constants import ChatAction
from openai import OpenAI
from fastapi import FastAPI, Request
import uvicorn

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

# URL for Healthchecks.io - CRUCIALE per il 24/7
HEALTHCHECKS_URL = os.getenv("HEALTHCHECKS_URL")

MODELLO_IA = "mistralai/mistral-small-3.2-24b-instruct:free"
SYSTEM_PROMPT = (
    "You are an AI assistant named 'Will', and you will always be ready to respond "
    "with short, accurate answers or long, detailed ones depending on the context. "
    # ... resto del SYSTEM_PROMPT
    "with the same language the user is speaking. If you're asked, you'll answer "
    "that your creator is lollo21, an italian indie developer."
)

# Porta fornita dall'ambiente di hosting
PORT = int(os.environ.get("PORT", 8080))
# URL pubblico del tuo servizio (ottenuto da Render)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- 3. Setup App Telegram ---
# (Il JobQueue è abilitato per l'esecuzione dei compiti schedulati)
persistence = PicklePersistence(filepath="bot_persistence.pickle")
application = (
    Application.builder()
    .token(TELEGRAM_TOKEN)
    .persistence(persistence)
    .build()
)

# --- FUNZIONE PER L'ANTI-SLEEP DI RENDER ---
def ping_healthcheck(context: ContextTypes.DEFAULT_TYPE):
    """Esegue un ping a Healthchecks.io per mantenere il servizio attivo su Render."""
    if not HEALTHCHECKS_URL:
        logger.warning("HEALTHCHECKS_URL non impostato. Saltando il ping anti-sleep.")
        return

    try:
        # Usiamo requests per fare una chiamata HTTP sincrona
        requests.get(HEALTHCHECKS_URL, timeout=5)
        logger.info("Ping a Healthchecks.io completato con successo (Bot kept awake).")
    except Exception as e:
        logger.error(f"Errore durante il ping a Healthchecks.io: {e}")

# --- 4. Gestori Comandi Telegram (invariati) ---

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

# --- 5. Registra gli Handlers e il JobQueue ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("setkey", set_key))
application.add_handler(CommandHandler("mykey", my_key))
application.add_handler(CommandHandler("delkey", del_key))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- 6. Setup Server FastAPI ---
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """All'avvio, inizializza il bot, imposta il webhook e aggiunge il ping job."""
    
    await application.initialize()
    
    # 1. Imposta il Webhook (come facevi prima, ma lo teniamo per sicurezza)
    if WEBHOOK_URL and TELEGRAM_TOKEN:
        webhook_path = f"/{TELEGRAM_TOKEN}"
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
        logger.info(f"Webhook set successfully to {WEBHOOK_URL}{webhook_path}")
    else:
        logger.warning("WEBHOOK_URL o TELEGRAM_TOKEN non impostato. Webhook non impostato automaticamente.")


    # 2. Aggiungi il Job Anti-Sleep di Healthchecks.io
    if HEALTHCHECKS_URL:
        # Esegui la funzione ping_healthcheck ogni 10 minuti (600 secondi)
        job_queue = application.job_queue
        # Lancia il job immediatamente, poi ripeti
        job_queue.run_repeating(ping_healthcheck, interval=600, first=1)
        logger.info("Anti-sleep job (Healthchecks) schedulato per ogni 10 minuti.")
    
    # 3. Avvia l'applicazione PTB (necessario per far funzionare i job)
    await application.start()       
    logger.info("✅ Bot application initialized and started.")


@app.on_event("shutdown")
async def shutdown_event():
    """Alla chiusura, ferma l'app PTB."""
    logger.info("Shutting down bot...")
    await application.stop()
    await application.bot.delete_webhook() # Rimuove il webhook
    await application.shutdown()
    logger.info("🛑 Bot shutdown complete.")

@app.post(f"/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    """Endpoint per ricevere gli update da Telegram."""
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
    """Endpoint per controllare se il server è attivo (ping render)."""
    return {"status": "ok", "bot_name": "Will AI Bot (Running)"}

# --- 7. Avvia il Server ---
if __name__ == "__main__":
    logger.info(f"Starting server on host 0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
