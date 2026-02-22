import asyncio
import random
import os
import json
import re
import base64
import sqlite3
from pathlib import Path
from groq import Groq
from telegram import Bot, Update
from telegram.error import TelegramError
from ddgs import DDGS

# ============ 1. الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise EnvironmentError("❌ تأكد من إضافة TELEGRAM_TOKEN و GROQ_API_KEY في Railway Variables")

client = Groq(api_key=GROQ_API_KEY)
DB_PATH = "/app/memory.db"

# ============ 2. قاعدة بيانات الذاكرة الدائمة ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (chat_id INTEGER, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS learned
                 (chat_id INTEGER, lesson TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def save_message(chat_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    # احتفظ بآخر 50 رسالة فقط لكل مستخدم
    c.execute("""DELETE FROM messages WHERE chat_id = ? AND rowid NOT IN 
                 (SELECT rowid FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 50)""",
              (chat_id, chat_id))
    conn.commit()
    conn.close()

def save_lesson(chat_id: int, lesson: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO learned (chat_id, lesson) VALUES (?, ?)", (chat_id, lesson))
    c.execute("""DELETE FROM learned WHERE chat_id = ? AND rowid NOT IN
                 (SELECT rowid FROM learned WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 20)""",
              (chat_id, chat_id))
    conn.commit()
    conn.close()

def get_history(chat_id: int, limit: int = 10) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?",
              (chat_id, limit))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return ""
    rows.reverse()
    return "\n".join([f"{r[0]}: {r[1]}" for r in rows])

def get_lessons(chat_id: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT lesson FROM learned WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 5", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return "\n".join([r[0] for r in rows]) if rows else ""

def clear_memory(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    c.execute("DELETE FROM learned WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

# ============ 3. الوكلاء ============
AGENTS = [
    {"name": "أحمد",  "emoji": "🔍", "role": "خبير البحث والمعلومات"},
    {"name": "سارة",  "emoji": "🤖", "role": "محللة بيانات وأرقام"},
    {"name": "خالد",  "emoji": "🌐", "role": "خبير تقني وتطبيقات"},
    {"name": "منى",   "emoji": "📊", "role": "استراتيجية وتخطيط"},
    {"name": "يوسف",  "emoji": "⚡", "role": "مطور برمجيات وأتمتة"},
]

# ============ 4. المتغيرات العامة ============
conversation_history: list[str] = []
discussion_active: bool = False
discussion_task: asyncio.Task | None = None
chat_id_global: int | None = None

# ============ 5. إرسال آمن ============
async def safe_send(bot: Bot, chat_id: int, text: str):
    try:
        # تقسيم الرسائل الطويلة
        if len(text) > 4000:
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await bot.send_message(chat_id=chat_id, text=chunk)
                await asyncio.sleep(0.5)
        else:
            await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"Send error: {e}")

# ============ 6. Groq النص ============
async def groq_generate(prompt: str, system: str, max_tokens: int = 512) -> str:
    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.7
            )
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq text error: {e}")
        return ""

# ============ 7. Groq الصوت (Whisper) ============
async def transcribe_audio(audio_bytes: bytes) -> str:
    try:
        transcription = await asyncio.to_thread(
            lambda: client.audio.transcriptions.create(
                file=("audio.ogg", audio_bytes, "audio/ogg"),
                model="whisper-large-v3",
                language="ar"
            )
        )
        return transcription.text.strip()
    except Exception as e:
        print(f"Whisper error: {e}")
        return ""

# ============ 8. Groq الصور (Vision) ============
async def analyze_image(image_bytes: bytes, question: str = "صف هذه الصورة بالتفصيل بالعربية") -> str:
    try:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-4-scout-17b-16e-instruct",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": question}
                    ]
                }],
                max_tokens=512
            )
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Vision error: {e}")
        return ""

# ============ 9. DuckDuckGo ============
async def search_web(query: str, max_results: int = 5) -> str:
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            return ""
        formatted = ""
        for i, r in enumerate(results, 1):
            formatted += f"{i}. {r.get('title','')}\n{r.get('body','')}\n\n"
        return formatted.strip()
    except Exception as e:
        print(f"DDG error: {e}")
        return ""

# ============ 10. جلب الصور ============
async def search_images(query: str) -> list[str]:
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().images(query, max_results=3))
        )
        return [r.get("image", "") for r in results if r.get("image")]
    except Exception as e:
        print(f"Image search error: {e}")
        return []

# ============ 11. تخطيط المهام ============
async def plan_task(user_request: str, chat_id: int) -> list[str]:
    history = get_history(chat_id, 5)
    system = """أنت مخطط مهام ذكي. قسّم الطلب إلى خطوات واضحة.
أجب بـ JSON فقط هكذا:
{"steps": ["خطوة 1", "خطوة 2", "خطوة 3"]}"""
    response = await groq_generate(
        f"السياق:\n{history}\n\nالطلب: {user_request}\nقسّمه إلى 3-5 خطوات.",
        system, 300
    )
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            steps = data.get("steps", [])
            if steps:
                return steps
    except Exception:
        pass
    return [user_request]

# ============ 12. تنفيذ خطوة ============
async def execute_step(step: str, chat_id: int, agent: dict) -> str:
    history = get_history(chat_id, 5)
    needs_search = any(w in step for w in ["ابحث", "اجلب", "معلومات", "أخبار", "سعر", "ما هو", "كيف", "من هو"])
    search_context = ""
    if needs_search:
        q = await groq_generate(f"استخرج كلمات البحث فقط من: {step}", "أخرج كلمات البحث فقط.", 50)
        search_context = await search_web(q)

    lessons = get_lessons(chat_id)
    prompt = f"""السياق:
{history}
{f"دروس مستفادة:{chr(10)}{lessons}" if lessons else ""}
{'نتائج البحث:\n' + search_context[:600] if search_context else ''}

المهمة: {step}
نفّذها الآن بشكل واضح:"""

    return await groq_generate(
        prompt,
        f"أنت {agent['emoji']} {agent['name']}، {agent['role']}. نفّذ المهمة بدقة واحترافية.",
        600
    )

# ============ 13. الوكيل الرئيسي ============
async def manus_agent(bot: Bot, chat_id: int, user_request: str):
    save_message(chat_id, "المستخدم", user_request)
    await safe_send(bot, chat_id, "🧠 الوكيل يفكر ويخطط...")

    steps = await plan_task(user_request, chat_id)

    if len(steps) > 1:
        steps_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(steps)])
        await safe_send(bot, chat_id, f"📋 خطة التنفيذ:\n\n{steps_text}")

    all_results = []
    for i, step in enumerate(steps):
        agent = AGENTS[i % len(AGENTS)]
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']} ينفذ الخطوة {i+1}:\n{step}")

        result = ""
        for _ in range(2):
            result = await execute_step(step, chat_id, agent)
            if result:
                break
            await asyncio.sleep(2)

        if result:
            all_results.append(f"{agent['name']}: {result}")
            save_message(chat_id, agent['name'], result)
            save_lesson(chat_id, f"نجحت في: {step[:80]}")
            await safe_send(bot, chat_id, f"✅ نتيجة الخطوة {i+1}:\n\n{result}")
        else:
            save_lesson(chat_id, f"فشلت في: {step[:80]}")
            await safe_send(bot, chat_id, f"⚠️ الخطوة {i+1} واجهت مشكلة، الوكيل يكمل...")

        await asyncio.sleep(1)

    if len(steps) > 1 and all_results:
        summary = await groq_generate(
            f"لخّص نتائج تنفيذ هذه المهمة:\nالطلب: {user_request}\nالنتائج: {chr(10).join(all_results[:3])}",
            "أنت مساعد يلخص النتائج بوضوح واحترافية.",
            400
        )
        if summary:
            await safe_send(bot, chat_id, f"📊 الملخص النهائي:\n\n{summary}")
            save_message(chat_id, "ملخص", summary)

    # هل يحتاج صور؟
    if any(w in user_request for w in ["صورة", "صور", "أرني", "اعرض"]):
        await safe_send(bot, chat_id, "🖼️ جاري جلب الصور...")
        image_urls = await search_images(user_request)
        for url in image_urls[:3]:
            try:
                await bot.send_photo(chat_id=chat_id, photo=url)
            except Exception:
                pass

# ============ 14. معالجة الصوت ============
async def handle_voice(bot: Bot, chat_id: int, file_id: str):
    await safe_send(bot, chat_id, "🎙️ جاري تحويل الصوت إلى نص...")
    try:
        file = await bot.get_file(file_id)
        audio_bytes = await file.download_as_bytearray()
        text = await transcribe_audio(bytes(audio_bytes))
        if text:
            await safe_send(bot, chat_id, f"🎙️ فهمت: {text}")
            await manus_agent(bot, chat_id, text)
        else:
            await safe_send(bot, chat_id, "❌ لم أتمكن من فهم الصوت، حاول مرة أخرى.")
    except Exception as e:
        await safe_send(bot, chat_id, f"❌ خطأ في معالجة الصوت: {str(e)[:100]}")

# ============ 15. معالجة الصور ============
async def handle_photo(bot: Bot, chat_id: int, photo, caption: str = ""):
    await safe_send(bot, chat_id, "🖼️ جاري تحليل الصورة...")
    try:
        file = await bot.get_file(photo[-1].file_id)
        image_bytes = await file.download_as_bytearray()
        question = caption if caption else "صف هذه الصورة بالتفصيل بالعربية"
        analysis = await analyze_image(bytes(image_bytes), question)
        if analysis:
            await safe_send(bot, chat_id, f"🖼️ تحليل الصورة:\n\n{analysis}")
            save_message(chat_id, "تحليل صورة", analysis)
        else:
            await safe_send(bot, chat_id, "❌ لم أتمكن من تحليل الصورة.")
    except Exception as e:
        await safe_send(bot, chat_id, f"❌ خطأ في تحليل الصورة: {str(e)[:100]}")

# ============ 16. النقاش التلقائي ============
async def run_discussion(bot: Bot):
    global discussion_active, conversation_history

    topics = [
        "مستقبل الذكاء الاصطناعي والوكلاء الذكيين",
        "كيف ستغير الأتمتة حياتنا اليومية",
        "مستقبل البرمجة مع الذكاء الاصطناعي",
        "الفرق بين الوكلاء الذكيين المختلفة",
        "تأثير التكنولوجيا على سوق العمل",
    ]

    current_topic = random.choice(topics)
    conversation_history = [f"الموضوع: {current_topic}"]

    await safe_send(bot, chat_id_global,
        f"💬 بدأ النقاش التلقائي\n\nالموضوع: {current_topic}\n\nاكتب اي رسالة للتدخل في النقاش")

    while discussion_active:
        agent = random.choice(AGENTS)
        context = "\n".join(conversation_history[-5:])

        response = await groq_generate(
            f"سياق النقاش:\n{context}\n\nماذا تقول الآن؟",
            f"أنت {agent['name']}، {agent['role']}. تحدث بشكل عفوي وطبيعي. جملة أو جملتان فقط. لا تقل اسمك.",
            150
        )

        if response:
            try:
                await safe_send(bot, chat_id_global, f"{agent['emoji']} {agent['name']}:\n{response}")
                conversation_history.append(f"{agent['name']}: {response}")
                if len(conversation_history) > 20:
                    conversation_history.pop(1)
            except TelegramError as e:
                print(f"Discussion error: {e}")
                break

        await asyncio.sleep(random.randint(20, 45))

# ============ 17. تدخل المستخدم في النقاش ============
async def handle_discussion_input(bot: Bot, chat_id: int, user_text: str):
    conversation_history.append(f"المستخدم: {user_text}")
    agent = random.choice(AGENTS)
    context = "\n".join(conversation_history[-5:])
    search_context = await search_web(user_text)
    search_note = f"\nمعلومة:\n{search_context[:400]}" if search_context else ""

    response = await groq_generate(
        f"السياق:\n{context}{search_note}\n\nرد على المستخدم: {user_text}",
        f"أنت {agent['name']}، {agent['role']}. رد بشكل مباشر وذكي في 2-3 جمل.",
        200
    )

    if response:
        await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']} يرد عليك:\n{response}")
        conversation_history.append(f"{agent['name']}: {response}")

# ============ 18. الحلقة الرئيسية ============
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

    print("🚀 الوكيل الذكي الكامل جاهز - LLaMA 3.3 + Whisper + Vision + ذاكرة دائمة")

    while True:
        try:
            updates = await bot.get_updates(offset=last_update_id, timeout=20)
            for update in updates:
                if not update.message:
                    continue
                last_update_id = update.update_id + 1
                chat_id = update.message.chat_id
                text = update.message.text or ""

                # معالجة الصوت
                if update.message.voice:
                    await handle_voice(bot, chat_id, update.message.voice.file_id)
                    continue

                # معالجة الصور
                if update.message.photo:
                    caption = update.message.caption or ""
                    await handle_photo(bot, chat_id, update.message.photo, caption)
                    continue

                if not text:
                    continue

                # الأوامر
                if text == "/start":
                    chat_id_global = chat_id
                    history = get_history(chat_id, 3)
                    greeting = "مرحباً من جديد! لا زلت أتذكر محادثاتنا السابقة 🧠" if history else "مرحباً! أنا وكيلك الذكي الجديد 🤖"
                    await safe_send(bot, chat_id, (
                        f"{greeting}\n\n"
                        "قدراتي:\n"
                        "• أخطط وأنفذ المهام خطوة بخطوة\n"
                        "• أبحث في الإنترنت تلقائياً\n"
                        "• أتذكر محادثاتنا حتى بعد إعادة التشغيل\n"
                        "• أفهم الرسائل الصوتية\n"
                        "• أحلل الصور\n"
                        "• أجلب الصور من الإنترنت\n\n"
                        "الأوامر:\n"
                        "/agent - وضع الوكيل الذكي\n"
                        "/discuss - وضع النقاش التلقائي\n"
                        "/memory - عرض ذاكرتي\n"
                        "/status - حالة النظام\n"
                        "/clear - مسح الذاكرة\n"
                        "/stop - إيقاف النقاش\n\n"
                        "ارسل نصاً او صوتاً او صورة وسأتعامل معها!"
                    ))

                elif text == "/agent":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, (
                        "🧠 وضع الوكيل الذكي مفعّل\n\n"
                        "يمكنك:\n"
                        "• ارسال نص لأي مهمة\n"
                        "• ارسال رسالة صوتية\n"
                        "• ارسال صورة مع سؤال\n"
                        "• طلب صور: اجلب صور قطط"
                    ))

                elif text == "/discuss":
                    chat_id_global = chat_id
                    discussion_active = True
                    if discussion_task is None or discussion_task.done():
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "النقاش يعمل بالفعل!")

                elif text == "/memory":
                    history = get_history(chat_id, 8)
                    lessons = get_lessons(chat_id)
                    if history or lessons:
                        msg = ""
                        if history:
                            msg += f"المحادثات الاخيرة:\n{history}\n\n"
                        if lessons:
                            msg += f"ما تعلمته:\n{lessons}"
                        await safe_send(bot, chat_id, f"🧠 ذاكرتي عنك:\n\n{msg[:2000]}")
                    else:
                        await safe_send(bot, chat_id, "🧠 ذاكرتي فارغة حتى الآن.")

                elif text == "/clear":
                    clear_memory(chat_id)
                    conversation_history.clear()
                    await safe_send(bot, chat_id, "🗑️ تم مسح الذاكرة كاملاً.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, "⏹ توقف النقاش.\n/discuss لإعادة النقاش\n/agent لتفعيل الوكيل")

                elif text == "/topic":
                    if discussion_active:
                        discussion_active = False
                        if discussion_task and not discussion_task.done():
                            discussion_task.cancel()
                        await asyncio.sleep(1)
                        discussion_active = True
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "ارسل /discuss اولاً.")

                elif text == "/status":
                    mode = "نقاش نشط 🟢" if discussion_active else "وكيل ذكي 🔵"
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
                    msg_count = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM learned WHERE chat_id = ?", (chat_id,))
                    lesson_count = c.fetchone()[0]
                    conn.close()
                    await safe_send(bot, chat_id, (
                        f"حالة النظام:\n\n"
                        f"الوضع: {mode}\n"
                        f"الرسائل المحفوظة: {msg_count}\n"
                        f"الدروس المتعلمة: {lesson_count}\n"
                        f"النموذج: LLaMA 3.3 70B\n"
                        f"الصوت: Whisper Large V3\n"
                        f"الرؤية: LLaMA 4 Scout"
                    ))

                else:
                    if discussion_active:
                        await handle_discussion_input(bot, chat_id, text)
                    else:
                        await manus_agent(bot, chat_id, text)

        except TelegramError as e:
            print(f"Telegram error: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
