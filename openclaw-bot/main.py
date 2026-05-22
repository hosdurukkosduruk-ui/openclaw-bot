"""
Ana başlatıcı dosya.
1. Keep-alive web server'ı başlatır (Render'ın uyumasını engeller)
2. Telegram bot'u başlatır
"""

from keep_alive import keep_alive
from bot import main as start_bot

if __name__ == "__main__":
    # Önce web server'ı başlat (Render için gerekli)
    keep_alive()
    
    # Sonra bot'u başlat
    start_bot()
