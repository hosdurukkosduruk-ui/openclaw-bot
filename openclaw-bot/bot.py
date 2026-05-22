import os
import json
import logging
import asyncio
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import httpx
from bs4 import BeautifulSoup

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OpenClawBot")

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")  # Telegram user ID for admin

# ============================================================
# DATA DIRECTORIES (Persistent Storage)
# ============================================================
DATA_DIR = Path("/app/data")
MEMORY_DIR = DATA_DIR / "memory"
SKILLS_DIR = DATA_DIR / "skills"
FILES_DIR = DATA_DIR / "files"

for d in [DATA_DIR, MEMORY_DIR, SKILLS_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# CORE FILES (OpenClaw-style)
# ============================================================
SOUL_FILE = DATA_DIR / "SOUL.md"
MEMORY_FILE = DATA_DIR / "MEMORY.md"
HEARTBEAT_FILE = DATA_DIR / "HEARTBEAT.md"
TASKS_FILE = DATA_DIR / "TASKS.json"
CRON_FILE = DATA_DIR / "CRON.json"

# Initialize core files if not exist
if not SOUL_FILE.exists():
    SOUL_FILE.write_text("""# SOUL - Kişilik Dosyası

## Kim
Sen 7/24 çalışan kişisel AI asistanısın. Adın "Claw".

## Kişilik
- Türkçe konuşuyorsun
- Samimi ve yardımsever
- Kısa ve öz cevaplar veriyorsun
- Emoji kullanıyorsun ama abartmıyorsun
- Teknik konularda detaylı, günlük konularda rahat

## Kurallar
- Kullanıcının tercihlerini hatırla ve MEMORY.md'ye kaydet
- Görevleri takip et
- Proaktif ol - hatırlatma zamanı geldiyse kendin mesaj at
- Her gün günlük log tut
""")

if not MEMORY_FILE.exists():
    MEMORY_FILE.write_text("# MEMORY - Uzun Süreli Hafıza\n\n_Henüz bir şey öğrenmedim._\n")

if not HEARTBEAT_FILE.exists():
    HEARTBEAT_FILE.write_text("# HEARTBEAT - Proaktif Görevler\n\n_Henüz proaktif görev yok._\n")

if not TASKS_FILE.exists():
    TASKS_FILE.write_text("[]")

if not CRON_FILE.exists():
    CRON_FILE.write_text("[]")

# ============================================================
# AI CLIENT
# ============================================================
client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

# ============================================================
# CONVERSATION MEMORY (Per User, Session-based)
# ============================================================
conversations = {}
MAX_HISTORY = 30

def get_conversation(user_id: int) -> list:
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]

def add_message(user_id: int, role: str, content: str):
    conv = get_conversation(user_id)
    conv.append({"role": role, "content": content})
    if len(conv) > MAX_HISTORY * 2:
        conversations[user_id] = conv[-MAX_HISTORY * 2:]

# ============================================================
# MEMORY SYSTEM (OpenClaw-style persistent memory)
# ============================================================
def read_soul():
    return SOUL_FILE.read_text() if SOUL_FILE.exists() else ""

def read_memory():
    return MEMORY_FILE.read_text() if MEMORY_FILE.exists() else ""

def append_memory(fact: str):
    current = read_memory()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_entry = f"\n- [{timestamp}] {fact}"
    MEMORY_FILE.write_text(current + new_entry)

def get_daily_log_path():
    today = datetime.now().strftime("%Y-%m-%d")
    return MEMORY_DIR / f"{today}.md"

def append_daily_log(entry: str):
    log_path = get_daily_log_path()
    timestamp = datetime.now().strftime("%H:%M")
    if log_path.exists():
        content = log_path.read_text()
    else:
        content = f"# Günlük Log - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    content += f"- [{timestamp}] {entry}\n"
    log_path.write_text(content)

def search_memory(query: str) -> str:
    results = []
    # Search MEMORY.md
    memory = read_memory()
    for line in memory.split("\n"):
        if query.lower() in line.lower():
            results.append(f"[MEMORY] {line.strip()}")
    # Search daily logs
    for log_file in sorted(MEMORY_DIR.glob("*.md"), reverse=True)[:7]:
        content = log_file.read_text()
        for line in content.split("\n"):
            if query.lower() in line.lower():
                results.append(f"[{log_file.stem}] {line.strip()}")
    return "\n".join(results[:10]) if results else "Hafızada bu konuyla ilgili bir şey bulunamadı."

# ============================================================
# TASK SYSTEM (To-do & Reminders)
# ============================================================
def load_tasks():
    try:
        return json.loads(TASKS_FILE.read_text())
    except:
        return []

def save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))

def add_task(title: str, due: str = None, user_id: int = 0):
    tasks = load_tasks()
    task = {
        "id": len(tasks) + 1,
        "title": title,
        "due": due,
        "status": "pending",
        "created": datetime.now().isoformat(),
        "user_id": user_id
    }
    tasks.append(task)
    save_tasks(tasks)
    return task

def complete_task(task_id: int):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            t["completed"] = datetime.now().isoformat()
    save_tasks(tasks)

def get_pending_tasks():
    tasks = load_tasks()
    return [t for t in tasks if t["status"] == "pending"]

# ============================================================
# CRON / SCHEDULED TASKS SYSTEM
# ============================================================
scheduler = None

def load_cron_jobs():
    try:
        return json.loads(CRON_FILE.read_text())
    except:
        return []

def save_cron_jobs(jobs):
    CRON_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))

def add_cron_job(name: str, schedule: str, prompt: str, chat_id: int):
    jobs = load_cron_jobs()
    job = {
        "id": len(jobs) + 1,
        "name": name,
        "schedule": schedule,
        "prompt": prompt,
        "chat_id": chat_id,
        "active": True,
        "created": datetime.now().isoformat()
    }
    jobs.append(job)
    save_cron_jobs(jobs)
    return job

# ============================================================
# WEB SEARCH & FETCH
# ============================================================
async def web_search(query: str) -> str:
    """DuckDuckGo HTML search"""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = await c.get(f"https://html.duckduckgo.com/html/?q={query}", headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result")[:5]:
                title_el = r.select_one(".result__title")
                snippet_el = r.select_one(".result__snippet")
                if title_el and snippet_el:
                    results.append(f"• {title_el.get_text(strip=True)}: {snippet_el.get_text(strip=True)}")
            return "\n".join(results) if results else "Sonuç bulunamadı."
    except Exception as e:
        return f"Arama hatası: {e}"

async def web_fetch(url: str) -> str:
    """Fetch and extract text from a URL"""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = await c.get(url, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:3000]
    except Exception as e:
        return f"Sayfa çekme hatası: {e}"

# ============================================================
# CODE EXECUTION (Sandboxed)
# ============================================================
def execute_code(code: str, language: str = "python") -> str:
    """Execute code in a sandboxed environment"""
    try:
        if language == "python":
            result = subprocess.run(
                ["python3", "-c", code],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            )
        elif language == "bash":
            result = subprocess.run(
                ["bash", "-c", code],
                capture_output=True, text=True, timeout=30
            )
        else:
            return f"Desteklenmeyen dil: {language}"
        
        output = result.stdout[:2000] if result.stdout else ""
        error = result.stderr[:1000] if result.stderr else ""
        
        if error and not output:
            return f"❌ Hata:\n```\n{error}\n```"
        elif output:
            return f"✅ Çıktı:\n```\n{output}\n```"
        else:
            return "✅ Kod çalıştırıldı (çıktı yok)."
    except subprocess.TimeoutExpired:
        return "❌ Kod çalıştırma zaman aşımına uğradı (30s limit)."
    except Exception as e:
        return f"❌ Çalıştırma hatası: {e}"

# ============================================================
# FILE SYSTEM
# ============================================================
def file_read(filename: str) -> str:
    filepath = FILES_DIR / filename
    if filepath.exists():
        return filepath.read_text()[:3000]
    return f"Dosya bulunamadı: {filename}"

def file_write(filename: str, content: str) -> str:
    filepath = FILES_DIR / filename
    filepath.write_text(content)
    return f"✅ Dosya yazıldı: {filename} ({len(content)} karakter)"

def file_list() -> str:
    files = list(FILES_DIR.glob("*"))
    if not files:
        return "📁 Dosya yok."
    return "📁 Dosyalar:\n" + "\n".join(f"• {f.name} ({f.stat().st_size} bytes)" for f in files)

# ============================================================
# TOOL DEFINITIONS (Function Calling via Prompt)
# ============================================================
TOOLS_DESCRIPTION = """
## Kullanılabilir Araçlar

Sen aşağıdaki araçları kullanabilirsin. Bir araç kullanman gerektiğinde, cevabında EXACT olarak şu formatı kullan:

[TOOL:tool_name:param1|param2]

### Araçlar:

1. **web_search** - İnternette arama yap
   Format: [TOOL:web_search:arama sorgusu]
   
2. **web_fetch** - Bir web sayfasının içeriğini çek
   Format: [TOOL:web_fetch:https://example.com]

3. **execute_python** - Python kodu çalıştır
   Format: [TOOL:execute_python:print("hello")]

4. **execute_bash** - Bash komutu çalıştır
   Format: [TOOL:execute_bash:ls -la]

5. **memory_save** - Uzun süreli hafızaya kaydet
   Format: [TOOL:memory_save:kaydedilecek bilgi]

6. **memory_search** - Hafızada ara
   Format: [TOOL:memory_search:arama terimi]

7. **file_read** - Dosya oku
   Format: [TOOL:file_read:dosya_adi.txt]

8. **file_write** - Dosya yaz
   Format: [TOOL:file_write:dosya_adi.txt|dosya içeriği buraya]

9. **file_list** - Dosyaları listele
   Format: [TOOL:file_list:]

10. **task_add** - Görev ekle
    Format: [TOOL:task_add:görev açıklaması|tarih (opsiyonel)]

11. **task_list** - Görevleri listele
    Format: [TOOL:task_list:]

12. **task_complete** - Görevi tamamla
    Format: [TOOL:task_complete:görev_id]

13. **schedule_add** - Zamanlanmış görev ekle (cron formatı)
    Format: [TOOL:schedule_add:isim|cron_ifadesi|yapılacak iş açıklaması]
    Cron: dakika saat gün ay haftanın_günü
    Örnek: [TOOL:schedule_add:Sabah Brifing|0 9 * * *|Günlük haber özeti hazırla ve gönder]

14. **schedule_list** - Zamanlanmış görevleri listele
    Format: [TOOL:schedule_list:]

Araç kullandıktan sonra sonucu kullanıcıya açıkla.
Birden fazla araç kullanabilirsin.
"""

# ============================================================
# TOOL EXECUTOR
# ============================================================
async def execute_tools(text: str, user_id: int, chat_id: int) -> tuple:
    """Parse and execute tools from AI response"""
    tool_pattern = r'\[TOOL:(\w+):(.*?)\]'
    matches = re.findall(tool_pattern, text, re.DOTALL)
    
    if not matches:
        return text, False
    
    results = {}
    for tool_name, params in matches:
        try:
            if tool_name == "web_search":
                result = await web_search(params.strip())
            elif tool_name == "web_fetch":
                result = await web_fetch(params.strip())
            elif tool_name == "execute_python":
                result = execute_code(params.strip(), "python")
            elif tool_name == "execute_bash":
                result = execute_code(params.strip(), "bash")
            elif tool_name == "memory_save":
                append_memory(params.strip())
                result = "✅ Hafızaya kaydedildi."
            elif tool_name == "memory_search":
                result = search_memory(params.strip())
            elif tool_name == "file_read":
                result = file_read(params.strip())
            elif tool_name == "file_write":
                parts = params.split("|", 1)
                if len(parts) == 2:
                    result = file_write(parts[0].strip(), parts[1])
                else:
                    result = "❌ Format: dosya_adi|içerik"
            elif tool_name == "file_list":
                result = file_list()
            elif tool_name == "task_add":
                parts = params.split("|")
                title = parts[0].strip()
                due = parts[1].strip() if len(parts) > 1 else None
                task = add_task(title, due, user_id)
                result = f"✅ Görev #{task['id']} eklendi: {title}"
            elif tool_name == "task_list":
                tasks = get_pending_tasks()
                if tasks:
                    result = "📋 Görevler:\n" + "\n".join(
                        f"  #{t['id']} {'⏰'+t['due'] if t.get('due') else ''} {t['title']}"
                        for t in tasks
                    )
                else:
                    result = "✅ Bekleyen görev yok!"
            elif tool_name == "task_complete":
                complete_task(int(params.strip()))
                result = f"✅ Görev #{params.strip()} tamamlandı!"
            elif tool_name == "schedule_add":
                parts = params.split("|")
                if len(parts) >= 3:
                    job = add_cron_job(parts[0].strip(), parts[1].strip(), parts[2].strip(), chat_id)
                    result = f"✅ Zamanlanmış görev #{job['id']} eklendi: {parts[0].strip()} ({parts[1].strip()})"
                    # Register with scheduler
                    await register_cron_job(job)
                else:
                    result = "❌ Format: isim|cron_ifadesi|açıklama"
            elif tool_name == "schedule_list":
                jobs = load_cron_jobs()
                active = [j for j in jobs if j.get("active")]
                if active:
                    result = "⏰ Zamanlanmış Görevler:\n" + "\n".join(
                        f"  #{j['id']} [{j['schedule']}] {j['name']}: {j['prompt']}"
                        for j in active
                    )
                else:
                    result = "Zamanlanmış görev yok."
            else:
                result = f"❌ Bilinmeyen araç: {tool_name}"
        except Exception as e:
            result = f"❌ Araç hatası ({tool_name}): {e}"
        
        results[f"[TOOL:{tool_name}:{params}]"] = result
    
    return results, True

# ============================================================
# BUILD SYSTEM PROMPT
# ============================================================
def build_system_prompt(user_id: int) -> str:
    soul = read_soul()
    memory = read_memory()
    heartbeat = HEARTBEAT_FILE.read_text() if HEARTBEAT_FILE.exists() else ""
    pending_tasks = get_pending_tasks()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    tasks_str = ""
    if pending_tasks:
        tasks_str = "\n## Bekleyen Görevler\n" + "\n".join(
            f"- #{t['id']} {t['title']}" + (f" (⏰ {t['due']})" if t.get('due') else "")
            for t in pending_tasks
        )
    
    daily_log = ""
    log_path = get_daily_log_path()
    if log_path.exists():
        daily_log = f"\n## Bugünün Logu\n{log_path.read_text()[-1000:]}"
    
    return f"""# SİSTEM
Bugünün tarihi ve saati: {today}

{soul}

## HAFIZA (Uzun Süreli)
{memory[-2000:]}

{tasks_str}

{daily_log}

{TOOLS_DESCRIPTION}

## ÖNEMLİ KURALLAR
1. Kullanıcı bir şey hatırlamanı isterse [TOOL:memory_save:...] kullan
2. Güncel bilgi gerekiyorsa [TOOL:web_search:...] kullan
3. Kod çalıştırman gerekiyorsa [TOOL:execute_python:...] veya [TOOL:execute_bash:...] kullan
4. Dosya işlemleri için file_read/write/list kullan
5. Görev/hatırlatma isterse task_add kullan
6. Zamanlanmış görev isterse schedule_add kullan
7. Her önemli etkileşimi günlük loga kaydet
8. Kullanıcının tercihlerini öğren ve hafızaya kaydet
9. Proaktif ol - görevler varsa hatırlat
"""

# ============================================================
# TELEGRAM HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    append_daily_log(f"Kullanıcı {user.first_name} botu başlattı.")
    
    # Save admin user ID
    global ADMIN_USER_ID
    if not ADMIN_USER_ID:
        ADMIN_USER_ID = str(user.id)
        os.environ["ADMIN_USER_ID"] = ADMIN_USER_ID
    
    await update.message.reply_text(
        f"🤖 Merhaba {user.first_name}! Ben **Claw** - senin 7/24 kişisel AI asistanın.\n\n"
        f"🧠 **OpenClaw Özellikleri:**\n"
        f"• 💬 Doğal konuşma + hafıza\n"
        f"• 🌐 İnternette arama yapma\n"
        f"• 💻 Kod yazma ve çalıştırma\n"
        f"• 📁 Dosya okuma/yazma\n"
        f"• ⏰ Zamanlanmış görevler (cron)\n"
        f"• 📋 To-do / hatırlatıcılar\n"
        f"• 🧠 Kalıcı hafıza (seni hatırlarım!)\n"
        f"• 📊 Günlük log tutma\n"
        f"• 🔄 Proaktif mesaj atma\n\n"
        f"📌 **Komutlar:**\n"
        f"/start - Başlat\n"
        f"/memory - Hafızamı göster\n"
        f"/tasks - Görevleri göster\n"
        f"/schedule - Zamanlanmış görevler\n"
        f"/files - Dosyaları göster\n"
        f"/soul - Kişiliğimi düzenle\n"
        f"/clear - Konuşma geçmişini temizle\n"
        f"/status - Sistem durumu\n"
        f"/help - Yardım\n\n"
        f"💬 Sadece yaz, ben hallederim!",
        parse_mode="Markdown"
    )

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = read_memory()
    if len(memory) > 3500:
        memory = memory[-3500:]
    await update.message.reply_text(f"🧠 **Hafıza:**\n\n{memory}", parse_mode="Markdown")

async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_pending_tasks()
    if not tasks:
        await update.message.reply_text("✅ Bekleyen görev yok!")
        return
    text = "📋 **Görevler:**\n\n"
    for t in tasks:
        due = f" ⏰ {t['due']}" if t.get('due') else ""
        text += f"• #{t['id']} {t['title']}{due}\n"
    text += "\nTamamlamak için: 'Görev #X tamamlandı' yaz"
    await update.message.reply_text(text, parse_mode="Markdown")

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = load_cron_jobs()
    active = [j for j in jobs if j.get("active")]
    if not active:
        await update.message.reply_text("⏰ Zamanlanmış görev yok.\n\nÖrnek: 'Her sabah 9da bana haber özeti gönder'")
        return
    text = "⏰ **Zamanlanmış Görevler:**\n\n"
    for j in active:
        text += f"• #{j['id']} **{j['name']}** [{j['schedule']}]\n  → {j['prompt']}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = file_list()
    await update.message.reply_text(result)

async def soul_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    soul = read_soul()
    if len(soul) > 3500:
        soul = soul[:3500]
    await update.message.reply_text(
        f"👻 **SOUL (Kişilik):**\n\n```\n{soul}\n```\n\nDeğiştirmek için: 'Kişiliğini şöyle değiştir: ...' yaz",
        parse_mode="Markdown"
    )

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    await update.message.reply_text("🗑️ Konuşma geçmişi temizlendi!")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_pending_tasks()
    jobs = [j for j in load_cron_jobs() if j.get("active")]
    memory_size = len(read_memory())
    log_count = len(list(MEMORY_DIR.glob("*.md")))
    file_count = len(list(FILES_DIR.glob("*")))
    
    await update.message.reply_text(
        f"📊 **Sistem Durumu:**\n\n"
        f"🟢 Bot: Aktif\n"
        f"🧠 Model: `{OPENAI_MODEL}`\n"
        f"🌐 API: `{OPENAI_BASE_URL}`\n"
        f"💾 Hafıza: {memory_size} karakter\n"
        f"📋 Bekleyen görevler: {len(tasks)}\n"
        f"⏰ Zamanlanmış görevler: {len(jobs)}\n"
        f"📝 Günlük log sayısı: {log_count}\n"
        f"📁 Dosya sayısı: {file_count}\n"
        f"🕐 Şu an: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Yardım - OpenClaw Bot**\n\n"
        "**Konuşma:**\n"
        "• Herhangi bir mesaj yaz → AI cevaplar\n"
        "• Seni hatırlar, tercihlerini öğrenir\n\n"
        "**İnternet:**\n"
        "• 'X hakkında araştır' → web araması yapar\n"
        "• 'Şu siteyi oku: URL' → sayfa içeriğini çeker\n\n"
        "**Kod:**\n"
        "• 'Şu Python kodunu çalıştır: ...' → kod çalıştırır\n"
        "• 'Bash komutu çalıştır: ...' → terminal komutu\n\n"
        "**Görevler:**\n"
        "• 'Yarın toplantıyı hatırlat' → görev ekler\n"
        "• 'Görevlerimi göster' → listeyi gösterir\n\n"
        "**Zamanlanmış:**\n"
        "• 'Her sabah 9da haber özeti gönder'\n"
        "• 'Her akşam 6da yapılacakları hatırlat'\n\n"
        "**Dosyalar:**\n"
        "• 'Bir dosya oluştur: notes.txt' → dosya yazar\n"
        "• 'notes.txt dosyasını oku' → dosya okur\n\n"
        "**Hafıza:**\n"
        "• 'Bunu hatırla: ...' → kalıcı hafızaya kaydeder\n"
        "• /memory → hafızayı gösterir",
        parse_mode="Markdown"
    )

# ============================================================
# MAIN MESSAGE HANDLER (ReAct Loop)
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    user_message = update.message.text
    
    # Log
    append_daily_log(f"{user_name}: {user_message[:100]}")
    
    # Typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Add to conversation
    add_message(user_id, "user", user_message)
    
    # Build context
    system_prompt = build_system_prompt(user_id)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(get_conversation(user_id))
    
    try:
        # STEP 1: Get AI response
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=3000,
            temperature=0.7
        )
        ai_response = response.choices[0].message.content
        
        # STEP 2: Check for tool calls (ReAct loop)
        tool_results, has_tools = await execute_tools(ai_response, user_id, chat_id)
        
        if has_tools:
            # Feed tool results back to AI for final response
            tool_context = "\n\n".join(
                f"Araç çağrısı: {call}\nSonuç: {result}" 
                for call, result in tool_results.items()
            )
            
            messages.append({"role": "assistant", "content": ai_response})
            messages.append({"role": "user", "content": f"[SİSTEM] Araç sonuçları:\n{tool_context}\n\nBu sonuçlara göre kullanıcıya cevap ver. Araç formatlarını ([TOOL:...]) tekrar kullanma, düz metin yaz."})
            
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            
            response2 = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                max_tokens=3000,
                temperature=0.7
            )
            final_response = response2.choices[0].message.content
        else:
            final_response = ai_response
        
        # Clean any remaining tool tags
        final_response = re.sub(r'\[TOOL:\w+:.*?\]', '', final_response).strip()
        
        # Save to conversation
        add_message(user_id, "assistant", final_response)
        
        # Log AI response
        append_daily_log(f"Claw: {final_response[:100]}...")
        
        # Send response (handle long messages)
        if len(final_response) > 4000:
            for i in range(0, len(final_response), 4000):
                await update.message.reply_text(final_response[i:i+4000])
        else:
            await update.message.reply_text(final_response)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Hata: {str(e)[:300]}\n\nTekrar deneyin.")

# ============================================================
# CRON JOB EXECUTOR
# ============================================================
async def execute_cron_job(job: dict, app: Application):
    """Execute a scheduled cron job"""
    try:
        logger.info(f"⏰ Cron job executing: {job['name']}")
        
        # Build prompt
        system_prompt = build_system_prompt(0)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[ZAMANLANMIŞ GÖREV] {job['prompt']}\n\nBu bir otomatik zamanlanmış görevdir. Görevi yerine getir ve sonucu bildir."}
        ]
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # Execute any tools
        tool_results, has_tools = await execute_tools(ai_response, 0, job['chat_id'])
        
        if has_tools:
            tool_context = "\n\n".join(
                f"Araç: {call}\nSonuç: {result}" 
                for call, result in tool_results.items()
            )
            messages.append({"role": "assistant", "content": ai_response})
            messages.append({"role": "user", "content": f"[SİSTEM] Araç sonuçları:\n{tool_context}\n\nSonuçları özetle."})
            
            response2 = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                max_tokens=2000
            )
            final = response2.choices[0].message.content
        else:
            final = ai_response
        
        final = re.sub(r'\[TOOL:\w+:.*?\]', '', final).strip()
        
        # Send to user
        await app.bot.send_message(
            chat_id=job['chat_id'],
            text=f"⏰ **{job['name']}**\n\n{final}",
            parse_mode="Markdown"
        )
        
        append_daily_log(f"[CRON] {job['name']}: Çalıştırıldı")
        
    except Exception as e:
        logger.error(f"Cron error: {e}")

async def register_cron_job(job: dict):
    """Register a job with APScheduler"""
    global scheduler
    if scheduler and job.get("active"):
        try:
            parts = job["schedule"].split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4]
                )
                scheduler.add_job(
                    execute_cron_job,
                    trigger,
                    args=[job, telegram_app],
                    id=f"cron_{job['id']}",
                    replace_existing=True
                )
                logger.info(f"✅ Cron registered: {job['name']} [{job['schedule']}]")
        except Exception as e:
            logger.error(f"Cron register error: {e}")

# ============================================================
# HEARTBEAT (Proactive check every 30 minutes)
# ============================================================
async def heartbeat_check(app: Application):
    """Proactive heartbeat - checks tasks, reminders"""
    try:
        tasks = get_pending_tasks()
        now = datetime.now()
        
        for task in tasks:
            if task.get("due"):
                try:
                    # Check if task is due
                    due_str = task["due"]
                    # Simple date parsing
                    if ":" in due_str:
                        due_dt = datetime.fromisoformat(due_str)
                    else:
                        due_dt = datetime.strptime(due_str, "%Y-%m-%d")
                    
                    if due_dt <= now and task.get("user_id"):
                        await app.bot.send_message(
                            chat_id=task["user_id"],
                            text=f"⏰ **Hatırlatma!**\n\n📋 Görev #{task['id']}: {task['title']}\n\nBu görev zamanı geldi!"
                        )
                        complete_task(task["id"])
                except:
                    pass
        
        append_daily_log(f"[HEARTBEAT] Kontrol yapıldı. {len(tasks)} bekleyen görev.")
        
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")

# ============================================================
# GLOBAL APP REFERENCE
# ============================================================
telegram_app = None

# ============================================================
# MAIN
# ============================================================
def main():
    global telegram_app, scheduler
    
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN is not set!")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set!")
    
    logger.info(f"🚀 OpenClaw Bot başlatılıyor...")
    logger.info(f"🧠 Model: {OPENAI_MODEL}")
    logger.info(f"🌐 API: {OPENAI_BASE_URL}")
    
    # Create Telegram app
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    telegram_app = app
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("soul", soul_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    
    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Initialize scheduler
    scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
    
    # Load existing cron jobs
    for job in load_cron_jobs():
        if job.get("active"):
            try:
                parts = job["schedule"].split()
                if len(parts) == 5:
                    trigger = CronTrigger(
                        minute=parts[0], hour=parts[1],
                        day=parts[2], month=parts[3], day_of_week=parts[4]
                    )
                    scheduler.add_job(
                        execute_cron_job, trigger,
                        args=[job, app], id=f"cron_{job['id']}",
                        replace_existing=True
                    )
                    logger.info(f"✅ Loaded cron: {job['name']}")
            except Exception as e:
                logger.error(f"Failed to load cron {job['name']}: {e}")
    
    # Heartbeat every 30 minutes
    scheduler.add_job(heartbeat_check, 'interval', minutes=30, args=[app])
    scheduler.start()
    
    logger.info("✅ OpenClaw Bot çalışıyor! 7/24 aktif.")
    logger.info(f"⏰ Scheduler aktif. {len(scheduler.get_jobs())} job yüklü.")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
