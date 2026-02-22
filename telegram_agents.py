import asyncio
import random
import os
import json
import re
import base64
import sqlite3
import hashlib
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
# 2. قاعدة البيانات  (ذاكرة دائمة + RAG + ملخصات)
# ================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # رسائل المحادثة
    c.execute("""CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, role TEXT, content TEXT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    # ملخصات دورية
    c.execute("""CREATE TABLE IF NOT EXISTS summaries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, summary TEXT,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    # قاعدة المعرفة RAG
    c.execute("""CREATE TABLE IF NOT EXISTS knowledge
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id INTEGER, title TEXT, content TEXT,
                  hash TEXT UNIQUE,
                  ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    # دروس مستفادة
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
    # احتفظ بآخر 60 رسالة
    c.execute("""DELETE FROM messages WHERE chat_id=? AND id NOT IN
                 (SELECT id FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT 60)""",
              (chat_id, chat_id))
    conn.commit()
    # كل 20 رسالة → اصنع ملخصاً
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
# 3. الوكلاء مع شخصيات واضحة
# ================================================================
AGENTS = [
    {"name": "أحمد",  "emoji": "🔍",
     "personality": "باحث دقيق يحب الحقائق والأدلة، يشكك في الأفكار السطحية"},
    {"name": "سارة",  "emoji": "🤖",
     "personality": "محللة بيانات تفكر بالأرقام والإحصاءات، تبحث عن الأنماط"},
    {"name": "خالد",  "emoji": "🌐",
     "personality": "خبير تقني عملي، يفكر في التطبيق والتنفيذ الفعلي"},
    {"name": "منى",   "emoji": "📊",
     "personality": "استراتيجية تفكر في الصورة الكبيرة والعواقب بعيدة المدى"},
    {"name": "يوسف",  "emoji": "⚡",
     "personality": "مطور إبداعي يبحث عن حلول غير تقليدية وأتمتة ذكية"},
]

# ================================================================
# 4. المتغيرات العامة
# ================================================================
discussion_history: list[str] = []
discussion_active  = False
discussion_task: asyncio.Task | None = None
chat_id_global: int | None = None

# ================================================================
# 5. أدوات مساعدة
# ================================================================
async def safe_send(bot: Bot, chat_id: int, text: str):
    if not text:
        return
    try:
        for i in range(0, len(text), 4000):
            await bot.send_message(chat_id=chat_id, text=text[i:i+4000])
            await asyncio.sleep(0.3)
    except Exception as e:
        print(f"Send error: {e}")

async def groq_call(prompt: str, system: str, max_tokens=600, temp=0.7) -> str:
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

async def search_images(query: str) -> list[str]:
    try:
        res = await asyncio.to_thread(lambda: list(DDGS().images(query, max_results=3)))
        return [r.get("image","") for r in res if r.get("image")]
    except:
        return []

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
# 6. بناء السياق الكامل (ذاكرة عميقة)
# ================================================================
def build_context(chat_id: int, query: str = "") -> str:
    ctx = ""
    # ملخصات قديمة
    summaries = get_summaries(chat_id)
    if summaries:
        ctx += f"=== ملخص المحادثات السابقة ===\n{summaries}\n\n"
    # RAG
    if query:
        docs = search_knowledge(chat_id, query)
        if docs:
            ctx += "=== من قاعدة المعرفة ===\n"
            for title, content in docs:
                ctx += f"[{title}]: {content[:400]}\n"
            ctx += "\n"
    # دروس
    lessons = get_lessons(chat_id)
    if lessons:
        ctx += f"=== دروس مستفادة ===\n{lessons}\n\n"
    # رسائل أخيرة
    recent = get_recent_msgs(chat_id, 10)
    if recent:
        ctx += f"=== المحادثة الأخيرة ===\n{recent}\n"
    return ctx

# ================================================================
# 7. تلخيص تلقائي كل 20 رسالة
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
# 8. التعاون الحقيقي بين الوكلاء (قلب النظام)
# ================================================================
async def agents_collaborate(bot: Bot, chat_id: int, question: str, context: str) -> str:
    """
    كل وكيل يقرأ آراء السابقين ويبني عليها أو يعارضها،
    ثم يوسف (المطور) يصنع الرد النهائي المتكامل.
    """
    await safe_send(bot, chat_id, "🤝 الوكلاء يتشاورون...")
    
    opinions: list[str] = []
    
    # كل وكيل (عدا الأخير) يبدي رأيه
    for agent in AGENTS[:-1]:
        prev = "\n".join(f"- {op}" for op in opinions) if opinions else "لا يوجد آراء سابقة"
        opinion = await groq_call(
            f"""السياق:
{context}

السؤال/المهمة: {question}

آراء زملائك حتى الآن:
{prev}

أبدِ رأيك من منظورك الخاص. يمكنك الموافقة أو الاختلاف أو الإضافة. جملتان أو ثلاث.""",
            f"أنت {agent['name']}، {agent['personality']}. أبدِ رأيك بصدق من منظورك.",
            200, 0.8
        )
        if opinion:
            opinions.append(f"{agent['emoji']} {agent['name']}: {opinion}")
            await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']}:\n{opinion}")
            await asyncio.sleep(0.5)
    
    # يوسف يصنع الرد النهائي المتكامل
    all_opinions = "\n\n".join(opinions)
    final = await groq_call(
        f"""السياق:
{context}

السؤال/المهمة: {question}

آراء الفريق:
{all_opinions}

بناءً على كل ما سبق، اصنع إجابة نهائية شاملة ومتكاملة تأخذ أفضل ما في كل رأي.""",
        f"أنت {AGENTS[-1]['name']}، {AGENTS[-1]['personality']}. اصنع الرد النهائي الأفضل.",
        800, 0.6
    )
    return final

# ================================================================
# 9. التفكير متعدد المراحل (Chain of Thought)
# ================================================================
async def chain_of_thought(question: str, context: str) -> str:
    """يفكر أولاً ثم يحسّن إجابته"""
    # المرحلة 1: تفكير أولي
    draft = await groq_call(
        f"السياق:\n{context}\n\nالسؤال: {question}\n\nفكّر بصوت عالٍ خطوة بخطوة:",
        "أنت مساعد يفكر بعمق. اعرض تفكيرك بالتفصيل.",
        400, 0.7
    )
    # المرحلة 2: تقييم ذاتي
    critique = await groq_call(
        f"هذا تفكيري الأولي:\n{draft}\n\nما نقاط ضعفه؟ ما الذي فاته؟",
        "أنت ناقد ذكي تجد الثغرات في التفكير.",
        200, 0.6
    )
    # المرحلة 3: الرد المحسّن
    final = await groq_call(
        f"""السياق:\n{context}
السؤال: {question}
التفكير الأولي: {draft}
نقد التفكير: {critique}

الآن أعطِ الإجابة النهائية المحسّنة:""",
        "أنت مساعد ذكي يقدم أفضل إجابة ممكنة بعد التفكير العميق.",
        600, 0.6
    )
    return final

# ================================================================
# 10. الوكيل الرئيسي (يجمع كل شيء)
# ================================================================
async def master_agent(bot: Bot, chat_id: int, user_input: str):
    count = save_msg(chat_id, "المستخدم", user_input)
    asyncio.create_task(maybe_summarize(chat_id, count))
    
    ctx = build_context(chat_id, user_input)
    
    # تحديد نوع المهمة
    is_complex = len(user_input) > 30 or any(
        w in user_input for w in ["قارن","حلل","خطط","ابحث عن","اشرح","كيف","لماذا","ما الفرق"]
    )
    needs_images = any(w in user_input for w in ["صورة","صور","أرني","اعرض"])
    needs_search = any(w in user_input for w in ["ابحث","اجلب","أخبار","سعر","أحدث","حديث"])
    
    search_ctx = ""
    if needs_search:
        await safe_send(bot, chat_id, "🔍 جاري البحث في الإنترنت...")
        search_q = await groq_call(f"استخرج كلمات البحث من: {user_input}", "أخرج كلمات البحث فقط.", 50)
        search_ctx = await web_search(search_q)
        if search_ctx:
            ctx += f"\n=== نتائج البحث ===\n{search_ctx[:800]}\n"
    
    if is_complex:
        # تعاون حقيقي + تفكير عميق
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        collab_result = await agents_collaborate(bot, chat_id, user_input, ctx)
        
        # تحسين الرد بـ Chain of Thought
        final = await chain_of_thought(user_input, ctx + f"\nرأي الفريق:\n{collab_result}")
        response = final if final else collab_result
    else:
        # رد سريع من وكيل واحد
        agent = random.choice(AGENTS)
        response = await groq_call(
            f"السياق:\n{ctx}\n\nالسؤال: {user_input}",
            f"أنت {agent['name']}، {agent['personality']}. أجب بشكل مباشر ومفيد.",
            500
        )
        await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']}:")
    
    if response:
        await safe_send(bot, chat_id, response)
        save_msg(chat_id, "الوكيل", response)
        save_lesson(chat_id, f"أجبت على: {user_input[:60]}")
    
    # جلب الصور إذا طُلبت
    if needs_images:
        urls = await search_images(user_input)
        for url in urls[:3]:
            try:
                await bot.send_photo(chat_id=chat_id, photo=url)
            except Exception:
                pass

# ================================================================
# 11. النقاش التلقائي المستمر
# ================================================================
async def run_discussion(bot: Bot):
    global discussion_active, discussion_history
    topics = [
        "مستقبل الذكاء الاصطناعي والوكلاء الذكيين",
        "هل ستحل الروبوتات محل البشر في سوق العمل؟",
        "الفرق بين الذكاء الاصطناعي العام والضيق",
        "أخلاقيات الذكاء الاصطناعي وحدوده",
        "مستقبل البرمجة مع وجود الذكاء الاصطناعي",
    ]
    topic = random.choice(topics)
    discussion_history = [f"الموضوع: {topic}"]
    await safe_send(bot, chat_id_global,
        f"💬 بدأ النقاش التلقائي\nالموضوع: {topic}\n\nاكتب أي رسالة للتدخل")

    while discussion_active:
        agent = random.choice(AGENTS)
        ctx   = "\n".join(discussion_history[-6:])
        # أحياناً يبحث في الإنترنت ليضيف معلومة حقيقية
        extra = ""
        if random.random() < 0.15:
            results = await web_search(topic, 2)
            if results:
                extra = f"\nمعلومة من الإنترنت:\n{results[:300]}"
        
        reply = await groq_call(
            f"سياق النقاش:\n{ctx}{extra}\n\nشارك بجملتين ذكيتين من منظورك.",
            f"أنت {agent['name']}، {agent['personality']}. تحدث بشكل عفوي. لا تقل اسمك.",
            120, 0.9
        )
        if reply:
            try:
                await safe_send(bot, chat_id_global, f"{agent['emoji']} {agent['name']}:\n{reply}")
                discussion_history.append(f"{agent['name']}: {reply}")
                if len(discussion_history) > 25:
                    discussion_history.pop(1)
            except TelegramError as e:
                print(f"Discussion TG error: {e}")
                break
        await asyncio.sleep(random.randint(20, 40))

async def handle_discussion_msg(bot: Bot, chat_id: int, text: str):
    discussion_history.append(f"المستخدم: {text}")
    agent = random.choice(AGENTS)
    ctx   = "\n".join(discussion_history[-5:])
    search_ctx = await web_search(text, 3)
    extra = f"\nمعلومة:\n{search_ctx[:400]}" if search_ctx else ""
    reply = await groq_call(
        f"السياق:\n{ctx}{extra}\n\nرد على المستخدم: {text}",
        f"أنت {agent['name']}، {agent['personality']}. رد بشكل مباشر في 2-3 جمل.",
        200
    )
    if reply:
        await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']} يرد:\n{reply}")
        discussion_history.append(f"{agent['name']}: {reply}")

# ================================================================
# 12. الحلقة الرئيسية
# ================================================================
async def main():
    global discussion_active, discussion_task, chat_id_global
    init_db()
    bot = Bot(token=TELEGRAM_TOKEN)

    last_update_id = None
    try:
        updates = await bot.get_updates(offset=-1, timeout=5)
        if updates:
            last_update_id = updates[-1].update_id + 1
    except Exception:
        pass

    print("🚀 النظام جاهز: تعاون وكلاء + ذاكرة عميقة + RAG + Chain of Thought")

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
                    file      = await bot.get_file(update.message.voice.file_id)
                    audio     = await file.download_as_bytearray()
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
                    file  = await bot.get_file(update.message.photo[-1].file_id)
                    img   = await file.download_as_bytearray()
                    q     = update.message.caption or "صف هذه الصورة بالتفصيل"
                    analysis = await analyze_image(bytes(img), q)
                    if analysis:
                        await safe_send(bot, chat_id, f"🖼️ تحليل الصورة:\n\n{analysis}")
                        save_msg(chat_id, "تحليل صورة", analysis)
                    continue

                # --- مستند / ملف نصي → RAG ---
                if update.message.document:
                    doc = update.message.document
                    if doc.mime_type in ("text/plain",):
                        await safe_send(bot, chat_id, "📄 جاري حفظ المستند في قاعدة المعرفة...")
                        file    = await bot.get_file(doc.file_id)
                        content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
                        title   = doc.file_name or "مستند"
                        added   = save_knowledge(chat_id, title, content)
                        if added:
                            await safe_send(bot, chat_id, f"✅ تم حفظ '{title}' في قاعدة المعرفة!\nيمكنني الآن الإجابة عن أسئلة تتعلق بمحتواه.")
                        else:
                            await safe_send(bot, chat_id, "ℹ️ هذا المستند موجود بالفعل في قاعدة المعرفة.")
                    else:
                        await safe_send(bot, chat_id, "⚠️ أدعم الملفات النصية (.txt) فقط حالياً.")
                    continue

                if not text:
                    continue

                # --- الأوامر ---
                if text == "/start":
                    chat_id_global = chat_id
                    recent = get_recent_msgs(chat_id, 2)
                    greet  = "مرحباً من جديد! لا أزال أتذكر محادثاتنا 🧠" if recent else "مرحباً! أنا وكيلك الذكي المتطور 🤖"
                    await safe_send(bot, chat_id, f"""{greet}

قدراتي:
• تعاون حقيقي بين 5 وكلاء لكل سؤال صعب
• ذاكرة دائمة مع تلخيص تلقائي كل 20 رسالة
• قاعدة معرفة شخصية (أرسل ملف .txt لأحفظه)
• تفكير عميق متعدد المراحل
• بحث في الإنترنت تلقائياً
• فهم الصوت وتحليل الصور
• جلب الصور من الإنترنت

الأوامر:
/agent  - وضع الوكيل الذكي
/discuss - وضع النقاش التلقائي
/knowledge - قاعدة معرفتي
/memory - ذاكرتي عنك
/status - حالة النظام
/clear  - مسح كل شيء
/stop   - إيقاف النقاش

أرسل نصاً أو صوتاً أو صورة أو ملف .txt وسأتعامل معها!""")

                elif text == "/agent":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, "🧠 وضع الوكيل الذكي مفعّل\n\nأرسل أي سؤال صعب وسيناقشه الفريق كاملاً قبل الإجابة!")

                elif text == "/discuss":
                    chat_id_global = chat_id
                    discussion_active = True
                    if discussion_task is None or discussion_task.done():
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "النقاش يعمل بالفعل!")

                elif text == "/knowledge":
                    docs = list_knowledge(chat_id)
                    if docs:
                        msg = "📚 قاعدة معرفتي:\n\n" + "\n".join(f"{i+1}. {d[1]}" for i, d in enumerate(docs))
                        await safe_send(bot, chat_id, msg)
                    else:
                        await safe_send(bot, chat_id, "📚 قاعدة المعرفة فارغة.\nأرسل ملف .txt لإضافته!")

                elif text == "/memory":
                    ctx = build_context(chat_id)
                    await safe_send(bot, chat_id, f"🧠 ذاكرتي:\n\n{ctx[:2500]}" if ctx else "🧠 الذاكرة فارغة.")

                elif text == "/clear":
                    clear_all(chat_id)
                    discussion_history.clear()
                    await safe_send(bot, chat_id, "🗑️ تم مسح كل شيء.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, "⏹ توقف النقاش.\n/discuss لإعادته\n/agent للوكيل الذكي")

                elif text == "/topic":
                    if discussion_active:
                        discussion_active = False
                        if discussion_task and not discussion_task.done():
                            discussion_task.cancel()
                        await asyncio.sleep(1)
                        discussion_active = True
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "أرسل /discuss أولاً.")

                elif text == "/status":
                    mode = "نقاش نشط 🟢" if discussion_active else "وكيل ذكي 🔵"
                    conn = sqlite3.connect(DB_PATH)
                    c    = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM messages  WHERE chat_id=?", (chat_id,))
                    msgs = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM summaries WHERE chat_id=?", (chat_id,))
                    sums = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM knowledge WHERE chat_id=?", (chat_id,))
                    docs = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM lessons   WHERE chat_id=?", (chat_id,))
                    lsns = c.fetchone()[0]
                    conn.close()
                    await safe_send(bot, chat_id, f"""حالة النظام:

الوضع: {mode}
الرسائل المحفوظة: {msgs}
الملخصات: {sums}
مستندات RAG: {docs}
دروس مستفادة: {lsns}

النماذج:
• LLaMA 3.3 70B  - التفكير والنصوص
• Whisper Large  - الصوت
• LLaMA 4 Scout  - الصور""")

                else:
                    if discussion_active:
                        await handle_discussion_msg(bot, chat_id, text)
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
