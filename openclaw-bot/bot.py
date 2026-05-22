import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "Sen yardımcı bir AI asistansın. Türkçe konuşuyorsun. Her konuda yardımcı oluyorsun. Kullanıcıya kısa ve öz cevaplar ver.")

# OpenAI client (custom base URL destekli)
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)

# Conversation memory (per user)
conversations = {}
MAX_HISTORY = 20  # Son 20 mesajı hatırla

def get_conversation(user_id: int) -> list:
    """Kullanıcının konuşma geçmişini getir"""
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]

def add_message(user_id: int, role: str, content: str):
    """Konuşma geçmişine mesaj ekle"""
    conv = get_conversation(user_id)
    conv.append({"role": role, "content": content})
    # Max history aşılırsa eski mesajları sil
    if len(conv) > MAX_HISTORY * 2:
        conversations[user_id] = conv[-MAX_HISTORY * 2:]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot başlatma komutu"""
    user = update.effective_user
    await update.message.reply_text(
        f"🤖 Merhaba {user.first_name}!\n\n"
        f"Ben senin 7/24 AI asistanınım. Bana her şeyi sorabilirsin!\n\n"
        f"📌 Komutlar:\n"
        f"/start - Başlat\n"
        f"/clear - Konuşma geçmişini temizle\n"
        f"/model - Kullanılan modeli göster\n"
        f"/help - Yardım\n\n"
        f"💬 Sadece mesaj yaz, ben cevaplayayım!"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Konuşma geçmişini temizle"""
    user_id = update.effective_user.id
    conversations[user_id] = []
    await update.message.reply_text("🗑️ Konuşma geçmişi temizlendi! Sıfırdan başlayabiliriz.")

async def model_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Model bilgisini göster"""
    await update.message.reply_text(
        f"🧠 Kullanılan model: `{OPENAI_MODEL}`\n"
        f"🌐 API: `{OPENAI_BASE_URL}`\n"
        f"💾 Hafıza: Son {MAX_HISTORY} mesaj hatırlanıyor\n"
        f"🟢 Durum: Aktif ve çalışıyor!",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yardım komutu"""
    await update.message.reply_text(
        "🆘 **Yardım**\n\n"
        "Bu bot 7/24 çalışan bir AI asistanıdır.\n\n"
        "• Herhangi bir mesaj yaz → AI cevap verir\n"
        "• /clear → Konuşma geçmişini sıfırla\n"
        "• /model → Hangi AI modeli kullanıldığını gör\n"
        "• /start → Botu yeniden başlat\n\n"
        "🔒 Her kullanıcının konuşması özeldir.\n"
        "🧠 Bot son mesajlarını hatırlar.",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcı mesajlarını işle"""
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # "Yazıyor..." göster
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Mesajı geçmişe ekle
    add_message(user_id, "user", user_message)
    
    try:
        # API çağrısı
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(get_conversation(user_id))
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # AI cevabını geçmişe ekle
        add_message(user_id, "assistant", ai_response)
        
        # Telegram mesaj limiti 4096 karakter
        if len(ai_response) > 4000:
            # Uzun mesajları parçala
            for i in range(0, len(ai_response), 4000):
                await update.message.reply_text(ai_response[i:i+4000])
        else:
            await update.message.reply_text(ai_response)
            
    except Exception as e:
        logger.error(f"API error: {e}")
        await update.message.reply_text(
            f"❌ Hata oluştu: {str(e)[:200]}\n\nTekrar deneyin."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hata yakalayıcı"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Bot'u başlat"""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN environment variable is not set!")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is not set!")
    
    logger.info(f"🚀 Bot başlatılıyor... Model: {OPENAI_MODEL}")
    logger.info(f"🌐 API Base URL: {OPENAI_BASE_URL}")
    
    # Application oluştur
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Komutları ekle
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("model", model_info))
    app.add_handler(CommandHandler("help", help_command))
    
    # Mesaj handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Hata handler
    app.add_error_handler(error_handler)
    
    # Bot'u çalıştır
    logger.info("✅ Bot çalışıyor! 7/24 aktif.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
