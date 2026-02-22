import asyncio
import random
import os
import json
import re
from groq import Groq
from telegram import Bot
from telegram.error import TelegramError
from duckduckgo_search import DDGS

# ============ 1. الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise EnvironmentError("❌ تأكد من إضافة TELEGRAM_TOKEN و GROQ_API_KEY في Railway Variables")

client = Groq(api_key=GROQ_API_KEY)

# ============ 2. الوكلاء ============
AGENTS = [
    {"name": "🔍 أحمد", "role": "خبير البحث والمعلومات"},
    {"name": "🤖 سارة", "role": "محللة بيانات وأرقام"},
    {"name": "🌐 خالد", "role": "خبير تقني وتطبيقات"},
    {"name": "📊 منى",  "role": "استراتيجية وتخطيط"},
    {"name": "⚡ يوسف", "role": "مطور برمجيات وأتمتة"},
]

# ============ 3. ذاكرة طويلة لكل مستخدم ============
class AgentMemory:
    def __init__(self):
        self.short_term: list[str] = []
        self.long_term: list[str] = []
        self.tasks: list[dict] = []
        self.learned: list[str] = []

    def add_message(self, msg: str):
        self.short_term.append(msg)
        if len(self.short_term) > 10:
            self.long_term.append(f"[ملخص]: {self.short_term.pop(0)}")
        if len(self.long_term) > 30:
            self.long_term.pop(0)

    def add_task(self, task: str, success: bool, result: str):
        self.tasks.append({"task": task, "success": success, "result": result[:200]})
        if len(self.tasks) > 20:
            self.tasks.pop(0)
        if success:
            self.learned.append(f"نجحت في: {task[:100]}")
        else:
            self.learned.append(f"فشلت في: {task[:100]} - سأحاول بطريقة مختلفة")
        if len(self.learned) > 15:
            self.learned.pop(0)

    def get_context(self) -> str:
        ctx = ""
        if self.long_term:
            ctx += "📚 الذاكرة الطويلة:\n" + "\n".join(self.long_term[-5:]) + "\n\n"
        if self.short_term:
            ctx += "💬 المحادثة الأخيرة:\n" + "\n".join(self.short_term[-5:]) + "\n\n"
        if self.learned:
            ctx += "🧠 ما تعلمته:\n" + "\n".join(self.learned[-5:]) + "\n\n"
        return ctx

# ============ 4. تخزين الذاكرة لكل مستخدم ============
memories: dict[int, AgentMemory] = {}

def get_memory(chat_id: int) -> AgentMemory:
    if chat_id not in memories:
        memories[chat_id] = AgentMemory()
    return memories[chat_id]

# ============ 5. النقاش التلقائي ============
conversation_history: list[str] = []
discussion_active: bool = False
discussion_task: asyncio.Task | None = None
chat_id_global: int | None = None

# ============ 6. Groq ============
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
        print(f"Groq error: {e}")
        return ""

# ============ 7. DuckDuckGo ============
async def search_web(query: str, max_results: int = 5) -> str:
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            return ""
        formatted = ""
        for i, r in enumerate(results, 1):
            formatted += f"{i}. {r.get('title','')}\n{r.get('body','')}\nالمصدر: {r.get('href','')}\n\n"
        return formatted.strip()
    except Exception as e:
        print(f"DDG error: {e}")
        return ""

# ============ 8. تخطيط المهام ============
async def plan_task(user_request: str, memory: AgentMemory) -> list[str]:
    context = memory.get_context()
    system = """أنت مخطط مهام ذكي. قسّم الطلب إلى خطوات واضحة.
أجب بـ JSON فقط:
{"steps": ["الخطوة 1", "الخطوة 2", "الخطوة 3"]}
لا تكتب أي شيء آخر غير JSON."""

    prompt = f"""السياق:
{context}

طلب المستخدم: {user_request}

قسّمه إلى 3-6 خطوات تنفيذية."""

    response = await groq_generate(prompt, system, max_tokens=300)
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("steps", [user_request])
    except Exception:
        pass
    return [user_request]

# ============ 9. تنفيذ خطوة واحدة ============
async def execute_step(step: str, memory: AgentMemory, agent: dict) -> str:
    context = memory.get_context()
    needs_search = any(word in step.lower() for word in [
        "ابحث", "اجلب", "اعرف", "معلومات", "أخبار", "سعر", "ما هو", "كيف", "search", "find"
    ])
    search_context = ""
    if needs_search:
        search_query = await groq_generate(
            f"استخرج كلمات البحث من: {step}",
            system="أخرج كلمات البحث فقط.",
            max_tokens=50
        )
        search_context = await search_web(search_query)

    system = f"""أنت {agent['name']}، {agent['role']}.
أنت وكيل ذكي ينفذ المهام بدقة واحترافية.
استخدم نتائج البحث إذا توفرت.
أجب بشكل واضح ومفيد."""

    prompt = f"""السياق:
{context}

{'نتائج البحث:\n' + search_context[:800] if search_context else ''}

المهمة: {step}

نفّذها الآن:"""

    result = await groq_generate(prompt, system, max_tokens=600)
    return result

# ============ 10. الوكيل الرئيسي ============
async def manus_agent(bot: Bot, chat_id: int, user_request: str):
    memory = get_memory(chat_id)
    memory.add_message(f"المستخدم: {user_request}")

    await bot.send_message(chat_id=chat_id, text="🧠 *الوكيل يفكر ويخطط...*", parse_mode="Markdown")

    steps = await plan_task(user_request, memory)

    if len(steps) > 1:
        steps_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(steps)])
        await bot.send_message(
            chat_id=chat_id,
            text=f"📋 *خطة التنفيذ:*\n\n{steps_text}",
            parse_mode="Markdown"
        )

    all_results = []
    for i, step in enumerate(steps):
        agent = AGENTS[i % len(AGENTS)]
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚙️ *{agent['name']} ينفذ الخطوة {i+1}:*\n_{step}_",
            parse_mode="Markdown"
        )

        result = ""
        for attempt in range(2):
            result = await execute_step(step, memory, agent)
            if result:
                break
            await asyncio.sleep(2)

        if result:
            all_results.append(f"{agent['name']}: {result}")
            memory.add_message(f"{agent['name']}: {result}")
            memory.add_task(step, True, result)
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ *نتيجة الخطوة {i+1}:*\n\n{result}",
                parse_mode="Markdown"
            )
        else:
            memory.add_task(step, False, "فشل")
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ *الخطوة {i+1} واجهت مشكلة، الوكيل يكمل...*",
                parse_mode="Markdown"
            )
        await asyncio.sleep(1)

    # ملخص نهائي
    if len(steps) > 1 and all_results:
        summary = await groq_generate(
            f"لخّص نتائج تنفيذ هذه المهمة:\nالطلب: {user_request}\nالنتائج: {chr(10).join(all_results[:3])}",
            system="أنت مساعد يلخص النتائج بوضوح.",
            max_tokens=400
        )
        if summary:
            await bot.send_message(
                chat_id=chat_id,
                text=f"📊 *الملخص النهائي:*\n\n{summary}",
                parse_mode="Markdown"
            )
            memory.add_message(f"ملخص: {summary}")

# ============ 11. النقاش التلقائي المستمر ============
async def run_discussion(bot: Bot):
    global discussion_active, conversation_history

    topics = [
        "مستقبل الذكاء الاصطناعي والوكلاء الذكيين",
        "كيف ستغير الأتمتة حياتنا اليومية؟",
        "أفضل استراتيجيات البحث على الإنترنت",
        "مستقبل البرمجة مع وجود الذكاء الاصطناعي",
        "الفرق بين الوكلاء الذكيين المختلفة",
    ]

    current_topic = random.choice(topics)
    conversation_history = [f"الموضوع: {current_topic}"]

    await bot.send_message(
        chat_id=chat_id_global,
        text=f"💬 *بدأ النقاش التلقائي*\n\n📌 *{current_topic}*\n\n_اكتب أي رسالة للتدخل_",
        parse_mode="Markdown"
    )

    while discussion_active:
        agent = random.choice(AGENTS)
        context = "\n".join(conversation_history[-5:])

        response = await groq_generate(
            f"سياق النقاش:\n{context}\n\nماذا تقول الآن؟",
            system=f"أنت {agent['name']}، {agent['role']}. جملة أو جملتان عفويتان ومثيرتان للنقاش. لا تقل اسمك.",
            max_tokens=150
        )

        if response:
            try:
                await bot.send_message(
                    chat_id=chat_id_global,
                    text=f"*{agent['name']}:*\n{response}",
                    parse_mode="Markdown"
                )
                conversation_history.append(f"{agent['name']}: {response}")
                if len(conversation_history) > 20:
                    conversation_history.pop(1)
            except TelegramError as e:
                print(f"Telegram error: {e}")
                break

        await asyncio.sleep(random.randint(20, 45))

# ============ 12. تدخل المستخدم في النقاش ============
async def handle_discussion_input(bot: Bot, chat_id: int, user_text: str):
    conversation_history.append(f"👤 المستخدم: {user_text}")
    agent = random.choice(AGENTS)
    context = "\n".join(conversation_history[-5:])
    search_context = await search_web(user_text)
    search_note = f"\nمعلومة: {search_context[:400]}" if search_context else ""

    response = await groq_generate(
        f"السياق:\n{context}{search_note}\n\nرد على المستخدم: {user_text}",
        system=f"أنت {agent['name']}، {agent['role']}. رد بشكل مباشر وذكي، 2-3 جمل.",
        max_tokens=200
    )

    if response:
        await bot.send_message(
            chat_id=chat_id,
            text=f"*{agent['name']} يرد عليك:*\n{response}",
            parse_mode="Markdown"
        )
        conversation_history.append(f"{agent['name']}: {response}")

# ============ 13. الحلقة الرئيسية ============
async def main():
    global discussion_active, discussion_task, chat_id_global

    bot = Bot(token=TELEGRAM_TOKEN)

    last_update_id = None
    try:
        updates = await bot.get_updates(offset=-1, timeout=5)
        if updates:
            last_update_id = updates[-1].update_id + 1
    except Exception:
        pass

    print("🚀 الوكيل الذكي جاهز - LLaMA 3.3 + ذاكرة طويلة + تخطيط ذكي")

    while True:
        try:
            updates = await bot.get_updates(offset=last_update_id, timeout=20)
            for update in updates:
                if not update.message or not update.message.text:
                    continue
                last_update_id = update.update_id + 1
                chat_id = update.message.chat_id
                text = update.message.text

                if text == "/start":
                    chat_id_global = chat_id
                    await bot.send_message(chat_id=chat_id, text=(
                        "🤖 *مرحباً! أنا وكيل ذكي متكامل*\n\n"
                        "🧠 *قدراتي:*\n"
                        "• أخطط وأنفذ المهام خطوة بخطوة\n"
                        "• أبحث في الإنترنت تلقائياً\n"
                        "• أتذكر كل محادثاتنا\n"
                        "• أتعلم من أخطائي\n\n"
                        "📌 *الأوامر:*\n"
                        "/agent - وضع الوكيل الذكي\n"
                        "/discuss - وضع النقاش التلقائي\n"
                        "/memory - عرض ذاكرتي\n"
                        "/status - حالة النظام\n"
                        "/clear - مسح الذاكرة\n"
                        "/stop - إيقاف النقاش\n\n"
                        "💡 *أرسل أي مهمة وسأنفذها!*"
                    ), parse_mode="Markdown")

                elif text == "/agent":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await bot.send_message(chat_id=chat_id, text=(
                        "🧠 *وضع الوكيل الذكي مفعّل*\n\n"
                        "أرسل أي مهمة وسأخطط لها وأنفذها!\n\n"
                        "_مثال: ابحث عن أفضل 5 لغات برمجة في 2025 وقارن بينها_"
                    ), parse_mode="Markdown")

                elif text == "/discuss":
                    chat_id_global = chat_id
                    discussion_active = True
                    if discussion_task is None or discussion_task.done():
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await bot.send_message(chat_id=chat_id, text="⚠️ النقاش يعمل بالفعل!")

                elif text == "/memory":
                    memory = get_memory(chat_id)
                    ctx = memory.get_context()
                    if ctx:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"🧠 *ذاكرتي عنك:*\n\n{ctx[:1000]}",
                            parse_mode="Markdown"
                        )
                    else:
                        await bot.send_message(chat_id=chat_id, text="🧠 ذاكرتي فارغة حتى الآن.")

                elif text == "/clear":
                    memories.pop(chat_id, None)
                    conversation_history.clear()
                    await bot.send_message(chat_id=chat_id, text="🗑️ تم مسح الذاكرة كاملاً.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await bot.send_message(chat_id=chat_id, text=(
                        "⏹ *توقف النقاش*\n\n"
                        "أرسل /discuss لإعادة النقاش\n"
                        "أو أرسل /agent لتفعيل الوكيل الذكي"
                    ), parse_mode="Markdown")

                elif text == "/topic":
                    if discussion_active:
                        discussion_active = False
                        if discussion_task and not discussion_task.done():
                            discussion_task.cancel()
                        await asyncio.sleep(1)
                        discussion_active = True
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await bot.send_message(chat_id=chat_id, text="⚠️ أرسل /discuss أولاً.")

                elif text == "/status":
                    memory = get_memory(chat_id)
                    status = "🟢 نشط" if discussion_active else "🔴 متوقف"
                    mode = "نقاش" if discussion_active else "وكيل ذكي"
                    await bot.send_message(chat_id=chat_id, text=(
                        f"*حالة النظام:*\n\n"
                        f"الوضع: {mode} {status}\n"
                        f"🧠 المهام المنجزة: {len(memory.tasks)}\n"
                        f"📚 الذكريات: {len(memory.long_term)}\n"
                        f"💡 الدروس المتعلمة: {len(memory.learned)}\n"
                        f"🔥 النموذج: LLaMA 3.3 70B"
                    ), parse_mode="Markdown")

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
