import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from openai import OpenAI # Sì, usiamo la libreria OpenAI per OpenRouter

# --- Configurazione Iniziale ---

# 1. Carica le variabili d'ambiente (le tue chiavi API) dal file .env
load_dotenv()

# 2. Abilita il logging per il debug
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 3. Leggi le chiavi API dalle variabili d'ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_TOKEN or not OPENROUTER_KEY:
    raise ValueError("Token Telegram o Chiave OpenRouter non trovati. Assicurati di aver creato e configurato correttamente il file .env")

# 4. Imposta il modello da usare su OpenRouter
# Scegli un modello dalla lista di OpenRouter (es. "mistralai/mistral-7b-instruct:free")
MODELLO_IA = "mistralai/mistral-7b-instruct:free" 

# 5. Configura il client OpenRouter
# Indichiamo alla libreria OpenAI di usare il server di OpenRouter
client_openrouter = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

# --- Gestori Comandi Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Invia un messaggio quando viene eseguito il comando /start."""
    user_name = update.effective_user.first_name
    await update.message.reply_html(
        f"Ciao {user_name}! 👋\n\nSono un bot collegato a OpenRouter. "
        f"Inviami un messaggio e io risponderò usando il modello:\n<b>{MODELLO_IA}</b>"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestisce i messaggi di testo e inoltra la richiesta a OpenRouter."""
    
    # Ignora i messaggi se non sono di testo
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    chat_id = update.message.chat_id

    # 1. Invia l'azione "typing..." per far sapere all'utente che stiamo lavorando
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        # 2. Chiama l'API di OpenRouter (usando l'interfaccia OpenAI)
        completion = client_openrouter.chat.completions.create(
            model=MODELLO_IA,
            messages=[
                {"role": "system", "content": "Sei un assistente AI utile e conciso."},
                {"role": "user", "content": user_text},
            ],
            # headers={ # Opzionale: per identificare la tua app su OpenRouter
            #     "HTTP-Referer": "LA_TUA_APP_URL", 
            #     "X-Title": "NOME_DELLA_TUA_APP", 
            # }
        )

        # 3. Estrai la risposta
        response_text = completion.choices[0].message.content

        # 4. Invia la risposta all'utente
        await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Errore durante la chiamata a OpenRouter: {e}")
        await update.message.reply_text("😔 Scusa, si è verificato un errore. Riprova più tardi.")


# --- Funzione Principale per Avviare il Bot ---

def main() -> None:
    """Avvia il bot e si mette in ascolto."""
    
    # 1. Crea l'applicazione bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # 2. Aggiungi i gestori per i comandi e i messaggi
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 3. Avvia il bot (polling)
    logger.info("Avvio del bot... Premi CTRL+C per terminare.")
    application.run_polling()


if __name__ == "__main__":
    main()