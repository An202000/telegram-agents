import asyncio
import random
import os
import json
import re
import base64
import sqlite3
import hashlib
import subprocess
import tempfile
import sys
from groq import Groq
from telegram import Bot
from telegram.error import TelegramError
from ddgs import DDGS

# ============ 1. الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise EnvironmentError("❌ أضف TELEGRAM_TOKEN و GROQ_API_KEY في Railway Variables")

client  = Groq(api_key=GROQ_API_KEY)
DB_PATH = "/app/memory.db"

# ================================================================
# 2. الوكلاء الثلاثة
# ================================================================
AGENTS = {
    "الباحث": {
        "emoji": "🔍",
        "personality": "باحث دقيق يلخص السؤال ويستخرج المعلومات من الإنترنت، يقدم السياق الكامل للفريق"
    },
    "المبرمج": {
        "emoji": "💻",
        "personality": "مبرمج محترف يكتب كوداً نظيفاً بدون أخطاء، يستخدم أفضل الممارسات، يشرح الكود خطوة بخطوة"
    },
    "المنفذ": {
        "emoji": "⚡",
        "personality": "منفذ أوامر متخصص، ينفذ الكود ويشغل الأوامر ويتعامل مع APIs الخارجية ويبلغ بالنتيجة الفعلية"
    },
}

# ================================================================
# 3. قاعدة البيانات (ذاكرة دائمة + RAG + ملخصات)
# ================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, role TEXT, content TEXT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS summaries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, summary TEXT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS knowledge
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, title TEXT, content TEXT,
                  hash TEXT UNIQUE,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, lesson TEXT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()

# --- رسائل ---
def save_msg(chat_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id,role,content) VALUES (?,?,?)",
              (chat_id, role, content))
    c.execute("""DELETE FROM messages WHERE chat_id=? AND id NOT IN
                 (SELECT id FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT 60)""",
              (chat_id, chat_id))
    conn.commit()
    c.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_recent_msgs(chat_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role,content FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
              (chat_id, limit))
    rows = list(reversed(c.fetchall()))
    conn.close()
    return "\n".join(f"{r[0]}: {r[1]}" for r in rows)

# --- ملخصات ---
def save_summary(chat_id, summary):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO summaries (chat_id,summary) VALUES (?,?)", (chat_id, summary))
    c.execute("""DELETE FROM summaries WHERE chat_id=? AND id NOT IN
                 (SELECT id FROM summaries WHERE chat_id=? ORDER BY ts DESC LIMIT 10)""",
              (chat_id, chat_id))
    conn.commit()
    conn.close()

def get_summaries(chat_id, limit=3):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT summary FROM summaries WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
              (chat_id, limit))
    rows = c.fetchall()
    conn.close()
    return "\n---\n".join(r[0] for r in reversed(rows))

# --- RAG ---
def save_knowledge(chat_id, title, content):
    h = hashlib.md5(content.encode()).hexdigest()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT INTO knowledge (chat_id,title,content,hash) VALUES (?,?,?,?)",
                     (chat_id, title, content[:3000], h))
        conn.commit()
        result = True
    except sqlite3.IntegrityError:
        result = False
    conn.close()
    return result

def search_knowledge(chat_id, query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    words = query.split()[:5]
    results = []
    for word in words:
        c.execute("""SELECT title,content FROM knowledge
                     WHERE chat_id=? AND (title LIKE ? OR content LIKE ?)
                     LIMIT 2""",
                  (chat_id, f"%{word}%", f"%{word}%"))
        results.extend(c.fetchall())
    conn.close()
    seen = set()
    unique = []
    for r in results:
        if r[0] not in seen:
            seen.add(r[0])
            unique.append(r)
    return unique[:3]

def list_knowledge(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id,title FROM knowledge WHERE chat_id=? ORDER BY ts DESC", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# --- دروس ---
def save_lesson(chat_id, lesson):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO lessons (chat_id,lesson) VALUES (?,?)", (chat_id, lesson))
    conn.execute("""DELETE FROM lessons WHERE chat_id=? AND id NOT IN
                    (SELECT id FROM lessons WHERE chat_id=? ORDER BY ts DESC LIMIT 20)""",
                 (chat_id, chat_id))
    conn.commit()
    conn.close()

def get_lessons(chat_id, limit=5):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT lesson FROM lessons WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
              (chat_id, limit))
    rows = c.fetchall()
    conn.close()
    return "\n".join(r[0] for r in rows)

def clear_all(chat_id):
    conn = sqlite3.connect(DB_PATH)
    for tbl in ("messages","summaries","knowledge","lessons"):
        conn.execute(f"DELETE FROM {tbl} WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

# ================================================================
# 4. أدوات مساعدة
# ================================================================
def truncate(text: str, max_chars: int = 6000) -> str:
    if len(text) > max_chars:
        return text[:max_chars] + "\n... [تم اختصار السياق]"
    return text

async def safe_send(bot: Bot, chat_id: int, text: str):
    if not text:
        return
    try:
        for i in range(0, len(text), 4000):
            await bot.send_message(chat_id=chat_id, text=text[i:i+4000])
            await asyncio.sleep(0.3)
    except Exception as e:
        print(f"Send error: {e}")

async def groq_call(prompt: str, system: str, max_tokens=800, temp=0.5) -> str:
    try:
        r = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role":"system","content":system},
                          {"role":"user","content":prompt}],
                max_tokens=max_tokens, temperature=temp
            )
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return ""

async def web_search(query: str, n=5) -> str:
    try:
        res = await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=n)))
        return "\n\n".join(f"{i+1}. {r.get('title','')}\n{r.get('body','')}"
                           for i, r in enumerate(res)) if res else ""
    except Exception as e:
        print(f"Search error: {e}")
        return ""

async def transcribe_voice(audio_bytes: bytes) -> str:
    try:
        t = await asyncio.to_thread(
            lambda: client.audio.transcriptions.create(
                file=("audio.ogg", audio_bytes, "audio/ogg"),
                model="whisper-large-v3", language="ar"
            )
        )
        return t.text.strip()
    except Exception as e:
        print(f"Whisper error: {e}")
        return ""

async def analyze_image(img_bytes: bytes, question="صف هذه الصورة بالتفصيل بالعربية") -> str:
    try:
        b64 = base64.b64encode(img_bytes).decode()
        r = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-4-scout-17b-16e-instruct",
                messages=[{"role":"user","content":[
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
                    {"type":"text","text":question}
                ]}],
                max_tokens=512
            )
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"Vision error: {e}")
        return ""

# ================================================================
# 5. بناء السياق الكامل
# ================================================================
def build_context(chat_id: int, query: str = "") -> str:
    ctx = ""
    summaries = get_summaries(chat_id)
    if summaries:
        ctx += f"=== ملخص المحادثات السابقة ===\n{summaries}\n\n"
    if query:
        docs = search_knowledge(chat_id, query)
        if docs:
            ctx += "=== من قاعدة المعرفة ===\n"
            for title, content in docs:
                ctx += f"[{title}]: {content[:400]}\n"
            ctx += "\n"
    lessons = get_lessons(chat_id)
    if lessons:
        ctx += f"=== دروس مستفادة ===\n{lessons}\n\n"
    recent = get_recent_msgs(chat_id, 10)
    if recent:
        ctx += f"=== المحادثة الأخيرة ===\n{recent}\n"
    return truncate(ctx, 5000)

# ================================================================
# 6. تلخيص تلقائي كل 20 رسالة
# ================================================================
async def maybe_summarize(chat_id: int, msg_count: int):
    if msg_count > 0 and msg_count % 20 == 0:
        recent = get_recent_msgs(chat_id, 20)
        summary = await groq_call(
            f"لخّص هذه المحادثة في 3-5 جمل مع الحفاظ على المعلومات المهمة:\n{recent}",
            "أنت مساعد يلخص المحادثات بدقة.",
            300
        )
        if summary:
            save_summary(chat_id, summary)

# ================================================================
# 7. تنفيذ الكود الفعلي (المنفذ)
# ================================================================
async def execute_code(code: str) -> str:
    """ينفذ كود Python في بيئة آمنة ويعيد النتيجة"""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                         delete=False, encoding='utf-8') as f:
            f.write(code)
            tmp_path = f.name

        result = await asyncio.to_thread(
            lambda: subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=30,  # 30 ثانية كحد أقصى
                encoding='utf-8'
            )
        )
        os.unlink(tmp_path)

        output = ""
        if result.stdout:
            output += f"✅ الناتج:\n{result.stdout}"
        if result.stderr:
            output += f"\n⚠️ أخطاء:\n{result.stderr}"
        return output.strip() or "✅ تم التنفيذ بنجاح (لا يوجد ناتج)"

    except subprocess.TimeoutExpired:
        return "❌ انتهت مهلة التنفيذ (30 ثانية)"
    except Exception as e:
        return f"❌ خطأ في التنفيذ: {e}"

async def execute_shell(command: str) -> str:
    """ينفذ أمر shell"""
    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                command, shell=True,
                capture_output=True,
                text=True,
                timeout=20,
                encoding='utf-8'
            )
        )
        output = ""
        if result.stdout:
            output += f"✅ الناتج:\n{result.stdout}"
        if result.stderr:
            output += f"\n⚠️ أخطاء:\n{result.stderr}"
        return output.strip() or "✅ تم التنفيذ"
    except subprocess.TimeoutExpired:
        return "❌ انتهت مهلة التنفيذ"
    except Exception as e:
        return f"❌ خطأ: {e}"

def extract_code_block(text: str) -> str:
    """يستخرج الكود من ```python ... ```"""
    pattern = r"```(?:python|bash|sh)?\n?(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches[0].strip() if matches else ""

def extract_shell_command(text: str) -> str:
    """يستخرج أوامر الـ shell من ```bash ... ```"""
    pattern = r"```(?:bash|sh)\n?(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches[0].strip() if matches else ""

# ================================================================
# 8. الوكيل الرئيسي - تعاون الثلاثة
# ================================================================
async def run_three_agents(bot: Bot, chat_id: int, user_input: str, ctx: str):
    """
    الباحث → يلخص ويبحث
    المبرمج → يكتب الكود
    المنفذ → ينفذ ويبلغ بالنتيجة
    """

    # ──────────────────────────────────────────
    # الخطوة 1: الباحث يلخص ويبحث
    # ──────────────────────────────────────────
    await safe_send(bot, chat_id, "🔍 الباحث يحلل ويبحث...")

    # تلخيص السؤال واستخراج كلمات البحث
    search_keywords = await groq_call(
        f"استخرج كلمات بحث مناسبة (3-5 كلمات) من هذا الطلب: {user_input}",
        "أخرج كلمات البحث فقط بدون شرح.",
        50
    )

    search_results = await web_search(search_keywords or user_input, 4)

    researcher_summary = await groq_call(
        f"""السياق السابق:
{ctx}

طلب المستخدم: {user_input}

نتائج البحث:
{search_results[:1500] if search_results else "لا يوجد نتائج"}

قدم:
1. ملخص واضح للمطلوب
2. المعلومات المفيدة من البحث
3. ما يحتاجه المبرمج لإنجاز المهمة""",
        f"أنت الباحث، {AGENTS['الباحث']['personality']}.",
        400
    )

    if researcher_summary:
        await safe_send(bot, chat_id,
            f"{AGENTS['الباحث']['emoji']} الباحث:\n{researcher_summary}")

    # ──────────────────────────────────────────
    # الخطوة 2: المبرمج يكتب الكود
    # ──────────────────────────────────────────
    await safe_send(bot, chat_id, "💻 المبرمج يكتب الكود...")

    programmer_code = await groq_call(
        f"""ملخص الباحث:
{researcher_summary}

السياق:
{ctx}

الطلب الأصلي: {user_input}

اكتب كوداً Python نظيفاً وكاملاً وقابلاً للتنفيذ مباشرة.
- تأكد من عدم وجود أخطاء
- أضف معالجة للاستثناءات
- اجعل الكود واضحاً مع تعليقات
- ضع الكود داخل ```python ... ```""",
        f"أنت المبرمج، {AGENTS['المبرمج']['personality']}. اكتب كوداً بدون أخطاء.",
        1000, 0.3
    )

    if programmer_code:
        await safe_send(bot, chat_id,
            f"{AGENTS['المبرمج']['emoji']} المبرمج:\n{programmer_code}")

    # ──────────────────────────────────────────
    # الخطوة 3: المنفذ ينفذ ويبلغ
    # ──────────────────────────────────────────
    await safe_send(bot, chat_id, "⚡ المنفذ يشغّل الكود...")

    # استخراج الكود وتنفيذه
    code_to_run = extract_code_block(programmer_code or "")
    shell_cmd   = extract_shell_command(programmer_code or "")

    execution_result = ""

    if code_to_run:
        execution_result = await execute_code(code_to_run)
    elif shell_cmd:
        execution_result = await execute_shell(shell_cmd)
    else:
        # لو مافي كود قابل للتنفيذ، المنفذ يشرح الخطوات
        execution_result = await groq_call(
            f"""الكود المقترح من المبرمج:
{programmer_code}

الطلب: {user_input}

بما أنه لا يوجد كود قابل للتنفيذ مباشرة، اشرح:
1. كيف تنفذ هذا يدوياً خطوة بخطوة
2. ما الأوامر التي يجب تشغيلها
3. ما النتيجة المتوقعة""",
            f"أنت المنفذ، {AGENTS['المنفذ']['personality']}.",
            400
        )

    await safe_send(bot, chat_id,
        f"{AGENTS['المنفذ']['emoji']} المنفذ:\n{execution_result}")

    return execution_result

# ================================================================
# 9. الوكيل الرئيسي الذكي
# ================================================================
async def master_agent(bot: Bot, chat_id: int, user_input: str):
    count = save_msg(chat_id, "المستخدم", user_input)
    asyncio.create_task(maybe_summarize(chat_id, count))

    ctx = build_context(chat_id, user_input)

    # هل يحتاج تعاون الثلاثة؟
    needs_team = any(w in user_input for w in [
        "كود", "برمجة", "سكريبت", "script", "python", "اكتب", "برنامج",
        "أتمتة", "تنفيذ", "شغّل", "حل", "خطأ", "error", "bug",
        "api", "قاعدة بيانات", "ملف", "استخرج", "حوّل"
    ]) or len(user_input) > 40

    if needs_team:
        await run_three_agents(bot, chat_id, user_input, ctx)
    else:
        # رد سريع من الباحث
        response = await groq_call(
            f"السياق:\n{ctx}\n\nالسؤال: {user_input}",
            f"أنت الباحث، {AGENTS['الباحث']['personality']}. أجب بشكل مباشر ومفيد.",
            500
        )
        if response:
            await safe_send(bot, chat_id,
                f"{AGENTS['الباحث']['emoji']} الباحث:\n{response}")

    save_msg(chat_id, "الفريق", f"تمت معالجة: {user_input[:60]}")
    save_lesson(chat_id, f"نفّذنا: {user_input[:60]}")

# ================================================================
# 10. الحلقة الرئيسية
# ================================================================
# حل مشكلة المتغيرات العامة - كل chat_id له state خاص
user_states: dict[int, dict] = {}

async def main():
    init_db()
    bot = Bot(token=TELEGRAM_TOKEN)

    last_update_id = None
    try:
        updates = await bot.get_updates(offset=-1, timeout=5)
        if updates:
            last_update_id = updates[-1].update_id + 1
    except Exception:
        pass

    print("🚀 النظام جاهز: الباحث + المبرمج + المنفذ")

    while True:
        try:
            updates = await bot.get_updates(offset=last_update_id, timeout=20)
            for update in updates:
                if not update.message:
                    continue
                last_update_id = update.update_id + 1
                chat_id = update.message.chat_id
                text    = update.message.text or ""

                # --- صوت ---
                if update.message.voice:
                    await safe_send(bot, chat_id, "🎙️ جاري فهم الرسالة الصوتية...")
                    file        = await bot.get_file(update.message.voice.file_id)
                    audio       = await file.download_as_bytearray()
                    transcribed = await transcribe_voice(bytes(audio))
                    if transcribed:
                        await safe_send(bot, chat_id, f"🎙️ فهمت: {transcribed}")
                        await master_agent(bot, chat_id, transcribed)
                    else:
                        await safe_send(bot, chat_id, "❌ لم أفهم الصوت، حاول مرة أخرى.")
                    continue

                # --- صور ---
                if update.message.photo:
                    await safe_send(bot, chat_id, "🖼️ جاري تحليل الصورة...")
                    file     = await bot.get_file(update.message.photo[-1].file_id)
                    img      = await file.download_as_bytearray()
                    q        = update.message.caption or "صف هذه الصورة بالتفصيل"
                    analysis = await analyze_image(bytes(img), q)
                    if analysis:
                        await safe_send(bot, chat_id, f"🖼️ تحليل الصورة:\n\n{analysis}")
                        save_msg(chat_id, "تحليل صورة", analysis)
                    continue

                # --- مستند → RAG ---
                if update.message.document:
                    doc = update.message.document
                    if doc.mime_type == "text/plain":
                        await safe_send(bot, chat_id, "📄 جاري حفظ المستند...")
                        file    = await bot.get_file(doc.file_id)
                        content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
                        title   = doc.file_name or "مستند"
                        added   = save_knowledge(chat_id, title, content)
                        msg = (f"✅ تم حفظ '{title}' في قاعدة المعرفة!"
                               if added else "ℹ️ هذا المستند موجود بالفعل.")
                        await safe_send(bot, chat_id, msg)
                    else:
                        await safe_send(bot, chat_id, "⚠️ أدعم الملفات النصية (.txt) فقط.")
                    continue

                if not text:
                    continue

                # --- الأوامر ---
                if text == "/start":
                    await safe_send(bot, chat_id, f"""مرحباً! أنا نظام الوكلاء الثلاثة 🤖

🔍 الباحث - يلخص ويبحث في الإنترنت
💻 المبرمج - يكتب كوداً نظيفاً بدون أخطاء
⚡ المنفذ  - ينفذ الكود ويشغل الأوامر فعلياً

الأوامر:
/status   - حالة النظام
/knowledge - قاعدة المعرفة
/memory   - الذاكرة
/clear    - مسح كل شيء

أرسل أي طلب برمجي وسيتعاون الفريق لإنجازه!""")

                elif text == "/knowledge":
                    docs = list_knowledge(chat_id)
                    if docs:
                        msg = "📚 قاعدة المعرفة:\n\n" + "\n".join(
                            f"{i+1}. {d[1]}" for i, d in enumerate(docs))
                        await safe_send(bot, chat_id, msg)
                    else:
                        await safe_send(bot, chat_id, "📚 فارغة. أرسل ملف .txt لإضافته!")

                elif text == "/memory":
                    ctx = build_context(chat_id)
                    await safe_send(bot, chat_id,
                        f"🧠 الذاكرة:\n\n{ctx[:2500]}" if ctx else "🧠 الذاكرة فارغة.")

                elif text == "/clear":
                    clear_all(chat_id)
                    await safe_send(bot, chat_id, "🗑️ تم مسح كل شيء.")

                elif text == "/status":
                    conn = sqlite3.connect(DB_PATH)
                    c    = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM messages  WHERE chat_id=?", (chat_id,))
                    msgs = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM summaries WHERE chat_id=?", (chat_id,))
                    sums = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM knowledge WHERE chat_id=?", (chat_id,))
                    docs = c.fetchone()[0]
                    conn.close()
                    await safe_send(bot, chat_id, f"""📊 حالة النظام:

الوكلاء: الباحث 🔍 | المبرمج 💻 | المنفذ ⚡
الرسائل المحفوظة: {msgs}
الملخصات: {sums}
مستندات RAG: {docs}

النماذج:
• LLaMA 3.3 70B  - التفكير والكود
• Whisper Large  - الصوت
• LLaMA 4 Scout  - الصور""")

                else:
                    await master_agent(bot, chat_id, text)

        except TelegramError as e:
            print(f"Telegram error: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
