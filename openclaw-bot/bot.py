import os
import json
import logging
import asyncio
import re
import http.server
import socketserver
from pathlib import Path
from threading import Thread
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")

# ============================================================
# DATA DIRECTORIES (Persistent Storage & Fallback)
# ============================================================
# Render'da disk bağlıysa /app/data kullanılır, yoksa yerel dizine kaydeder
DATA_DIR = Path("/app/data")
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("./data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_DIR = DATA_DIR / "memory"
SKILLS_DIR = DATA_DIR / "skills"
FILES_DIR = DATA_DIR / "files"

for d in [MEMORY_DIR, SKILLS_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# CORE FILES
SOUL_FILE = DATA_DIR / "SOUL.md"
MEMORY_FILE = DATA_DIR / "MEMORY.md"
HEARTBEAT_FILE = DATA_DIR / "HEARTBEAT.md"
TASKS_FILE = DATA_DIR / "TASKS.json"
CRON_FILE = DATA_DIR / "CRON.json"

# Initialize core files
if not SOUL_FILE.exists():
    SOUL_FILE.write_text("""# SOUL - Kişilik Dosyası
## Kim
Sen 7/24 çalışan kişisel AI asistanısın. Adın "Claw".
## Kişilik
- Türkçe konuşuyorsun.
- Samimi ve yardımsever, kısa ve öz cevaplar veriyorsun.
- Emoji kullanıyorsun ama abartmıyorsun.
## Kurallar
- Kullanıcı tercihlerini MEMORY.md'ye kaydet.
- Görevleri takip et ve proaktif ol.
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
# ASYNC AI CLIENT
# ============================================================
client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

# ============================================================
# CONVERSATION MEMORY (Session-based)
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
# MEMORY SYSTEM
# ============================================================
def read_soul():
    return SOUL_FILE.read_text(encoding="utf-8") if SOUL_FILE.exists() else ""

def read_memory():
    return MEMORY_FILE.read_text(encoding="utf-8") if MEMORY_FILE.exists() else ""

def append_memory(fact: str):
    current = read_memory()
    timestamp = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    new_entry = f"\n- [{timestamp}] {fact}"
    MEMORY_FILE.write_text(current + new_entry, encoding="utf-8")

def get_daily_log_path():
    today = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    return MEMORY_DIR / f"{today}.md"

def append_daily_log(entry: str):
    log_path = get_daily_log_path()
    timestamp = datetime.now(timezone(timedelta(hours=3))).strftime("%H:%M")
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
    else:
        content = f"# Günlük Log - {datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d')}\n\n"
    content += f"- [{timestamp}] {entry}\n"
    log_path.write_text(content, encoding="utf-8")

def search_memory(query: str) -> str:
    results = []
    memory = read_memory()
    for line in memory.split("\n"):
        if query.lower() in line.lower():
            results.append(f"[MEMORY] {line.strip()}")
    for log_file in sorted(MEMORY_DIR.glob("*.md"), reverse=True)[:7]:
        content = log_file.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if query.lower() in line.lower():
                results.append(f"[{log_file.stem}] {line.strip()}")
    return "\n".join(results[:10]) if results else "Hafızada bilgi bulunamadı."

# ============================================================
# TASK SYSTEM
# ============================================================
def load_tasks():
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

def add_task(title: str, due: str = None, user_id: int = 0):
    tasks = load_tasks()
    task = {
        "id": len(tasks) + 1,
        "title": title,
        "due": due,
        "status": "pending",
        "created": datetime.now(timezone(timedelta(hours=3))).isoformat(),
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
            t["completed"] = datetime.now(timezone(timedelta(hours=3))).isoformat()
    save_tasks(tasks)

def get_pending_tasks():
    tasks = load_tasks()
    return [t for t in tasks if t["status"] == "pending"]

# ============================================================
# CRON SYSTEM
# ============================================================
scheduler = None

def load_cron_jobs():
    try:
        return json.loads(CRON_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_cron_jobs(jobs):
    CRON_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

def add_cron_job(name: str, schedule: str, prompt: str, chat_id: int):
    jobs = load_cron_jobs()
    job = {
        "id": len(jobs) + 1,
        "name": name,
        "schedule": schedule,
        "prompt": prompt,
        "chat_id": chat_id,
        "active": True,
        "created": datetime.now(timezone(timedelta(hours=3))).isoformat()
    }
    jobs.append(job)
    save_cron_jobs(jobs)
    return job

# ============================================================
# WEB SEARCH & FETCH
# ============================================================
async def web_search(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
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
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = await c.get(url, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:3000]
    except Exception as e:
        return f"Sayfa çekme hatası: {e}"

# ============================================================
# ASYNC CODE EXECUTION (Non-blocking)
# ============================================================
async def execute_code(code: str, language: str = "python") -> str:
    try:
        if language == "python":
            cmd = ["python3", "-c", code]
        elif language == "bash":
            cmd = ["bash", "-c", code]
        else:
            return f"Desteklenmeyen dil: {language}"
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="ignore").strip()[:2000]
            error = stderr.decode("utf-8", errors="ignore").strip()[:1000]
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except:
                pass
            return "❌ Kod çalıştırma zaman aşımına uğradı (30s limit)."

        if error and not output:
            return f"❌ Hata:\n```\n{error}\n```"
        elif output:
            return f"✅ Çıktı:\n```\n{output}\n```"
        else:
            return "✅ Kod çalıştırıldı (çıktı yok)."
    except Exception as e:
        return f"❌ Çalıştırma hatası: {e}"

# ============================================================
# FILE SYSTEM
# ============================================================
def file_read(filename: str) -> str:
    filepath = FILES_DIR / filename
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")[:3000]
    return f"Dosya bulunamadı: {filename}"

def file_write(filename: str, content: str) -> str:
    filepath = FILES_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return f"✅ Dosya yazıldı: {filename} ({len(content)} karakter)"

def file_list() -> str:
    files = list(FILES_DIR.glob("*"))
    if not files:
        return "📁 Dosya yok."
    return "📁 Dosyalar:\n" + "\n".join(f"• {f.name} ({f.stat().st_size} bytes)" for f in files)

# ============================================================
# TOOLS DESCRIPTION
# ============================================================
TOOLS_DESCRIPTION = """
## Kullanılabilir Araçlar
Bir araç kullanman gerektiğinde, cevabında EXACT olarak şu formatı kullan:
[TOOL:tool_name:param1|param2]

Araçlar:
1. **web_search** - İnternet araması -> [TOOL:web_search:arama sorgusu]
2. **web_fetch** - URL içeriği çek -> [TOOL:web_fetch:https://example.com]
3. **execute_python** - Python kodu çalıştır -> [TOOL:execute_python:print("hello")]
4. **execute_bash** - Bash komutu çalıştır -> [TOOL:execute_bash:ls -la]
5. **memory_save** - Uzun süreli hafızaya kaydet -> [TOOL:memory_save:bilgi]
6. **memory_search** - Hafızada ara -> [TOOL:memory_search:arama terimi]
7. **file_read** - Dosya oku -> [TOOL:file_read:dosya.txt]
8. **file_write** - Dosya yaz -> [TOOL:file_write:dosya.txt|içerik]
9. **file_list** - Dosyaları listele -> [TOOL:file_list:]
10. **task_add** - Görev ekle -> [TOOL:task_add:görev|tarih]
11. **task_list** - Görevleri listele -> [TOOL:task_list:]
12. **task_complete** - Görevi tamamla -> [TOOL:task_complete:id]
13. **schedule_add** - Zamanlanmış görev -> [TOOL:schedule_add:isim|cron_ifadesi|açıklama] (Cron: dak sa gün ay haf)
14. **schedule_list** - Zamanlanmışları listele -> [TOOL:schedule_list:]
"""

# ============================================================
# TOOL EXECUTOR
# ============================================================
async def execute_tools(text: str, user_id: int, chat_id: int) -> tuple:
    tool_pattern = r'\[TOOL:(\w+):(.*?)\]'
    matches = re.findall(tool_pattern, text, re.DOTALL)
    
    if not matches:
        return {}, False
    
    results = {}
    for tool_name, params in matches:
        try:
            if tool_name == "web_search":
                result = await web_search(params.strip())
            elif tool_name == "web_fetch":
                result = await web_fetch(params.strip())
            elif tool_name == "execute_python":
                result = await execute_code(params.strip(), "python")
            elif tool_name == "execute_bash":
                result = await execute_code(params.strip(), "bash")
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
                    result = "📋 Görevler:\n" + "\n".join(f"  #{t['id']} {'⏰'+t['due'] if t.get('due') else ''} {t['title']}" for t in tasks)
                else:
                    result = "✅ Bekleyen görev yok!"
            elif tool_name == "task_complete":
                complete_task(int(params.strip()))
                result = f"✅ Görev #{params.strip()} tamamlandı!"
            elif tool_name == "schedule_add":
                parts = params.split("|")
                if len(parts) >= 3:
                    job = add_cron_job(parts[0].strip(), parts[1].strip(), parts[2].strip(), chat_id)
                    result = f"✅ Zamanlanmış görev #{job['id']} eklendi: {parts[0].strip()}"
                    await register_cron_job(job)
                else:
                    result = "❌ Format: isim|cron|açıklama"
            elif tool_name == "schedule_list":
                jobs = load_cron_jobs()
                active = [j for j in jobs if j.get("active")]
                if active:
                    result = "⏰ Zamanlanmış Görevler:\n" + "\n".join(f"  #{j['id']} [{j['schedule']}] {j['name']}" for j in active)
                else:
                    result = "Zamanlanmış görev yok."
            else:
                result = f"❌ Bilinmeyen araç: {tool_name}"
        except Exception as e:
            result = f"❌ Araç hatası ({tool_name}): {e}"
        
        results[f"[TOOL:{tool_name}:{params}]"] = result
    
    return results, True

# ============================================================
# SYSTEM PROMPT BUILDER
# ============================================================
def build_system_prompt(user_id: int) -> str:
    soul = read_soul()
    memory = read_memory()
    pending_tasks = get_pending_tasks()
    today = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    
    tasks_str = ""
    if pending_tasks:
        tasks_str = "\n## Bekleyen Görevler\n" + "\n".join(f"- #{t['id']} {t['title']}" for t in pending_tasks)
    
    daily_log = ""
    log_path = get_daily_log_path()
    if log_path.exists():
        daily_log = f"\n## Bugünün Logu\n{log_path.read_text(encoding='utf-8')[-1000:]}"
    
    return f"""# SİSTEM
Bugünün tarihi ve saati: {today}
{soul}
## HAFIZA (Uzun Süreli)
{memory[-2000:]}
{tasks_str}
{daily_log}
{TOOLS_DESCRIPTION}
"""

# ============================================================
# TELEGRAM HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    append_daily_log(f"Kullanıcı {user.first_name} botu başlattı.")
    
    global ADMIN_USER_ID
    if not ADMIN_USER_ID:
        ADMIN_USER_ID = str(user.id)
    
    await update.message.reply_text(
        f"🤖 Merhaba {user.first_name}! Ben **Claw** - 7/24 aktif asistanın.\n\n"
        f"🧠 /help yazarak özelliklerimi görebilirsin.",
        parse_mode="Markdown"
    )

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = read_memory()
    await update.message.reply_text(f"🧠 **Hafıza:**\n\n{memory[-3500:]}", parse_mode="Markdown")

async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_pending_tasks()
    if not tasks:
        await update.message.reply_text("✅ Bekleyen görev yok!")
        return
    text = "📋 **Görevler:**\n\n" + "\n".join(f"• #{t['id']} {t['title']}" for t in tasks)
    await update.message.reply_text(text, parse_mode="Markdown")

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = load_cron_jobs()
    active = [j for j in jobs if j.get("active")]
    if not active:
        await update.message.reply_text("⏰ Zamanlanmış görev yok.")
        return
    text = "⏰ **Görevler:**\n\n" + "\n".join(f"• #{j['id']} **{j['name']}** [{j['schedule']}]" for j in active)
    await update.message.reply_text(text, parse_mode="Markdown")

async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(file_list())

async def soul_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👻 **SOUL:**\n\n```\n{read_soul()[:3500]}\n```", parse_mode="Markdown")

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations[update.effective_user.id] = []
    await update.message.reply_text("🗑️ Geçmiş temizlendi!")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🟢 Bot: Aktif\n🧠 Model: `{OPENAI_MODEL}`\n🕐 Saat: {datetime.now(timezone(timedelta(hours=3))).strftime('%H:%M')}",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Komutlar:**\n/start - Başlat\n/memory - Hafıza\n/tasks - Görevler\n/schedule - Zamanlanmışlar\n/files - Dosyalar\n/clear - Geçmişi Temizle"
    )

# ============================================================
# MAIN MESSAGE HANDLER (ReAct Loop)
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    user_message = update.message.text
    
    append_daily_log(f"{user_name}: {user_message[:100]}")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    add_message(user_id, "user", user_message)
    
    system_prompt = build_system_prompt(user_id)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(get_conversation(user_id))
    
    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=3000,
            temperature=0.7
        )
        ai_response = response.choices[0].message.content
        
        tool_results, has_tools = await execute_tools(ai_response, user_id, chat_id)
        
        if has_tools:
            tool_context = "\n\n".join(f"Araç çağrısı: {call}\nSonuç: {result}" for call, result in tool_results.items())
            messages.append({"role": "assistant", "content": ai_response})
            messages.append({"role": "user", "content": f"[SİSTEM] Araç sonuçları:\n{tool_context}\n\nKullanıcıya nihai cevabı üret."})
            
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            response2 = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                max_tokens=3000,
                temperature=0.7
            )
            final_response = response2.choices[0].message.content
        else:
            final_response = ai_response
        
        final_response = re.sub(r'\[TOOL:\w+:.*?\]', '', final_response).strip()
        add_message(user_id, "assistant", final_response)
        append_daily_log(f"Claw: {final_response[:100]}...")
        
        if len(final_response) > 4000:
            for i in range(0, len(final_response), 4000):
                await update.message.reply_text(final_response[i:i+4000])
        else:
            await update.message.reply_text(final_response)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Hata: {str(e)[:300]}")

# ============================================================
# CRON & HEARTBEAT EXECUTORS
# ============================================================
async def execute_cron_job(job: dict, app: Application):
    try:
        logger.info(f"⏰ Cron tetiklendi: {job['name']}")
        system_prompt = build_system_prompt(0)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[ZAMANLANMIŞ GÖREV] {job['prompt']}"}
        ]
        
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=2000
        )
        final = re.sub(r'\[TOOL:\w+:.*?\]', '', response.choices[0].message.content).strip()
        
        await app.bot.send_message(chat_id=job['chat_id'], text=f"⏰ **{job['name']}**\n\n{final}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Cron hatası: {e}")

async def register_cron_job(job: dict):
    global scheduler
    if scheduler and job.get("active"):
        try:
            parts = job["schedule"].split()
            if len(parts) == 5:
                trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
                scheduler.add_job(
                    execute_cron_job,
                    trigger,
                    args=[job, telegram_app],
                    id=f"cron_{job['id']}",
                    replace_existing=True
                )
                logger.info(f"✅ Cron eklendi: {job['name']}")
        except Exception as e:
            logger.error(f"Cron ekleme hatası: {e}")

async def heartbeat_check(app: Application):
    try:
        tasks = get_pending_tasks()
        now = datetime.now(timezone(timedelta(hours=3)))
        for task in tasks:
            if task.get("due"):
                try:
                    due_dt = datetime.fromisoformat(task["due"]) if ":" in task["due"] else datetime.strptime(task["due"], "%Y-%m-%d")
                    if due_dt <= now and task.get("user_id"):
                        await app.bot.send_message(
                            chat_id=task["user_id"],
                            text=f"⏰ **Hatırlatma!**\n\n📋 Görev #{task['id']}: {task['title']}"
                        )
                        complete_task(task["id"])
                except Exception:
                    pass
        append_daily_log(f"[HEARTBEAT] Kontrol tamamlandı. Bekleyen görev: {len(tasks)}")
    except Exception as e:
        logger.error(f"Heartbeat hatası: {e}")

# ============================================================
# RENDER.COM HEALTH CHECK WEB SERVER (Crucial)
# ============================================================
def start_health_check_server():
    """Render.com'un port dinleme zorunluluğunu karşılamak için mini web sunucu"""
    port = int(os.getenv("PORT", "8080"))
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - Claw Bot is Alive")
    
    def run():
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            logger.info(f"Port {port} üzerinde Sağlık Sunucusu başlatıldı.")
            httpd.serve_forever()
            
    t = Thread(target=run, daemon=True)
    t.start()

# ============================================================
# MAIN INITIALIZATION
# ============================================================
telegram_app = None

def main():
    global telegram_app, scheduler
    
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN tanımlı değil!")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY tanımlı değil!")
    
    # Render web server start
    start_health_check_server()
    
    logger.info("🚀 OpenClaw Bot başlatılıyor...")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    telegram_app = app
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("soul", soul_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Scheduler Setup (AsyncIOScheduler for async compatibility)
    scheduler = AsyncIOScheduler(timezone="Europe/Istanbul")
    
    # Load Existing Jobs
    for job in load_cron_jobs():
        if job.get("active"):
            try:
                parts = job["schedule"].split()
                if len(parts) == 5:
                    trigger = CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
                    scheduler.add_job(
                        execute_cron_job, trigger,
                        args=[job, app], id=f"cron_{job['id']}",
                        replace_existing=True
                    )
                    logger.info(f"✅ Yüklenen Cron: {job['name']}")
            except Exception as e:
                logger.error(f"Cron yükleme hatası ({job['name']}): {e}")
                
    scheduler.add_job(heartbeat_check, 'interval', minutes=30, args=[app])
    scheduler.start()
    
    logger.info("✅ Bot Aktif / Polling Başlıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
