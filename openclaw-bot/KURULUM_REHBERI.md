# 🤖 7/24 AI Telegram Bot - Kurulum Rehberi

## Toplam Maliyet: $0

---

## ADIM 1: Telegram Bot Oluştur (2 dk)

1. Telegram'da **@BotFather** yaz
2. `/newbot` komutunu gönder
3. Bot'a bir isim ver (örn: "Murathan AI")
4. Bot'a bir username ver (örn: "murathan_ai_bot")
5. BotFather sana bir **token** verecek, şuna benzer:
   ```
   7123456789:AAHfiqksKZ8WmR2zMh5gTRUqEz0Z1xXxXxX
   ```
6. **BU TOKEN'I KOPYALA VE SAKLA!**

---

## ADIM 2: GitHub Repo Oluştur ve Dosyaları Yükle (3 dk)

1. github.com → "+" → "New repository"
2. İsim: `openclaw-bot`
3. Public seç → "Create repository"
4. Bu klasördeki TÜM dosyaları repo'ya yükle:
   - bot.py
   - keep_alive.py
   - main.py
   - requirements.txt
   - Dockerfile
   - render.yaml
   - .gitignore

---

## ADIM 3: Render.com'a Kayıt Ol (2 dk)

1. **render.com** adresine git
2. **"Get Started for Free"** tıkla
3. **"GitHub"** ile giriş yap (kart sormaz!)
4. GitHub hesabını bağla

---

## ADIM 4: Render'da Deploy Et (3 dk)

1. Dashboard'da **"New +"** → **"Web Service"** tıkla
2. **"Build and deploy from a Git repository"** seç
3. GitHub repo'nu bağla → `openclaw-bot` seç
4. Ayarlar:
   - **Name:** ai-telegram-bot
   - **Region:** Frankfurt (EU)
   - **Runtime:** Docker
   - **Instance Type:** Free
5. **Environment Variables** ekle:
   - `TELEGRAM_TOKEN` = (BotFather'dan aldığın token)
   - `OPENAI_API_KEY` = (senin OpenAI API key'in)
   - `OPENAI_MODEL` = gpt-4o-mini
   - `PORT` = 10000
6. **"Create Web Service"** tıkla
7. Bekle... 2-3 dk içinde deploy olacak ✅

---

## ADIM 5: Uyumayı Engelle - Cron Job Kur (2 dk)

Render free tier 15 dk boşta kalırsa uyur.
Bunu engellemek için ücretsiz cron servisi kullanacağız:

1. **cron-job.org** adresine git
2. Ücretsiz hesap aç
3. "Create Cronjob" tıkla:
   - **URL:** `https://ai-telegram-bot-XXXX.onrender.com/health`
     (Render'daki servis URL'ni yaz)
   - **Schedule:** Every 5 minutes
   - **Save**
4. Bu her 5 dk'da bir bot'a ping atarak uyumasını engeller ✅

---

## 🎉 TAMAM!

Artık Telegram'da botuna mesaj yazabilirsin!
- PC'ni kapat → bot çalışmaya devam eder
- Gece uyu → bot cevap vermeye devam eder
- 7/24 aktif! 🚀

---

## Ek Ayarlar

### Modeli değiştirmek:
Render Dashboard → Environment → OPENAI_MODEL değerini değiştir:
- `gpt-4o-mini` (ucuz, hızlı)
- `gpt-4o` (güçlü)
- `gpt-4-turbo` (çok güçlü)

### System prompt değiştirmek:
SYSTEM_PROMPT environment variable'ını düzenle.
Bot'un kişiliğini, bilgi alanını, konuşma tarzını belirler.
