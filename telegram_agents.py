import asyncio
import random
import os
import json
import re
from groq import Groq
from telegram import Bot
from telegram.error import TelegramError
from ddgs import DDGS

# ============ 1. الإعدادات ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise EnvironmentError("❌ تأكد من إضافة TELEGRAM_TOKEN و GROQ_API_KEY في Railway Variables")

client = Groq(api_key=GROQ_API_KEY)

# ============ 2. الوكلاء ============
AGENTS = [
    {"name": "احمد", "emoji": "🔍", "role": "خبير البحث والمعلومات"},
    {"name": "سارة", "emoji": "🤖", "role": "محللة بيانات وأرقام"},
    {"name": "خالد", "emoji": "🌐", "role": "خبير تقني وتطبيقات"},
    {"name": "منى",  "emoji": "📊", "role": "استراتيجية وتخطيط"},
    {"name": "يوسف", "emoji": "⚡", "role": "مطور برمجيات وأتمتة"},
]

# ============ 3. ذاكرة طويلة ============
class AgentMemory:
    def __init__(self):
        self.short_term: list[str] = []
        self.long_term: list[str] = []
        self.tasks: list[dict] = []
        self.learned: list[str] = []

    def add_message(self, msg: str):
        self.short_term.append(msg)
        if len(self.short_term) > 10:
            self.long_term.append(self.short_term.pop(0))
        if len(self.long_term) > 30:
            self.long_term.pop(0)

    def add_task(self, task: str, success: bool, result: str):
        self.tasks.append({"task": task, "success": success, "result": result[:200]})
        if len(self.tasks) > 20:
            self.tasks.pop(0)
        if success:
            self.learned.append(f"نجحت في: {task[:80]}")
        else:
            self.learned.append(f"فشلت في: {task[:80]}")
        if len(self.learned) > 15:
            self.learned.pop(0)

    def get_context(self) -> str:
        ctx = ""
        if self.short_term:
            ctx += "المحادثة الاخيرة:\n" + "\n".join(self.short_term[-5:]) + "\n\n"
        if self.learned:
            ctx += "ما تعلمته:\n" + "\n".join(self.learned[-3:]) + "\n\n"
        return ctx

# ============ 4. تخزين الذاكرة ============
memories: dict[int, AgentMemory] = {}

def get_memory(chat_id: int) -> AgentMemory:
    if chat_id not in memories:
        memories[chat_id] = AgentMemory()
    return memories[chat_id]

# ============ 5. المتغيرات العامة ============
conversation_history: list[str] = []
discussion_active: bool = False
discussion_task: asyncio.Task | None = None
chat_id_global: int | None = None

# ============ 6. إرسال رسالة آمنة (بدون Markdown أحياناً) ============
async def safe_send(bot: Bot, chat_id: int, text: str, use_markdown: bool = False):
    try:
        if use_markdown:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        # إذا فشل Markdown أرسل بدونه
        try:
            clean = text.replace("*", "").replace("_", "").replace("`", "")
            await bot.send_message(chat_id=chat_id, text=clean)
        except Exception as e:
            print(f"Send error: {e}")

# ============ 7. Groq ============
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

# ============ 8. DuckDuckGo (المكتبة الجديدة ddgs) ============
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

# ============ 9. تخطيط المهام ============
async def plan_task(user_request: str, memory: AgentMemory) -> list[str]:
    context = memory.get_context()
    system = """أنت مخطط مهام. قسّم الطلب إلى خطوات.
أجب بـ JSON فقط هكذا بالضبط:
{"steps": ["خطوة 1", "خطوة 2", "خطوة 3"]}"""

    response = await groq_generate(
        f"السياق:\n{context}\nالطلب: {user_request}\nقسّمه إلى 3-5 خطوات.",
        system,
        max_tokens=300
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

# ============ 10. تنفيذ خطوة ============
async def execute_step(step: str, memory: AgentMemory, agent: dict) -> str:
    context = memory.get_context()
    needs_search = any(w in step for w in ["ابحث", "اجلب", "معلومات", "أخبار", "سعر", "ما هو", "كيف"])
    search_context = ""
    if needs_search:
        q = await groq_generate(f"استخرج كلمات البحث فقط من: {step}", "أخرج كلمات البحث فقط بدون شرح.", 50)
        search_context = await search_web(q)

    prompt = f"""السياق:
{context}
{'نتائج البحث:\n' + search_context[:600] if search_context else ''}

المهمة: {step}
نفّذها الآن بشكل واضح:"""

    return await groq_generate(
        prompt,
        f"أنت {agent['emoji']} {agent['name']}، {agent['role']}. نفّذ المهمة بدقة واحترافية.",
        600
    )

# ============ 11. الوكيل الرئيسي ============
async def manus_agent(bot: Bot, chat_id: int, user_request: str):
    memory = get_memory(chat_id)
    memory.add_message(f"المستخدم: {user_request}")

    await safe_send(bot, chat_id, "🧠 الوكيل يفكر ويخطط...")

    steps = await plan_task(user_request, memory)

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
            result = await execute_step(step, memory, agent)
            if result:
                break
            await asyncio.sleep(2)

        if result:
            all_results.append(f"{agent['name']}: {result}")
            memory.add_message(f"{agent['name']}: {result}")
            memory.add_task(step, True, result)
            await safe_send(bot, chat_id, f"✅ نتيجة الخطوة {i+1}:\n\n{result}")
        else:
            memory.add_task(step, False, "فشل")
            await safe_send(bot, chat_id, f"⚠️ الخطوة {i+1} واجهت مشكلة، الوكيل يكمل...")

        await asyncio.sleep(1)

    # ملخص نهائي
    if len(steps) > 1 and all_results:
        summary = await groq_generate(
            f"لخّص هذه النتائج بوضوح:\nالطلب: {user_request}\nالنتائج: {chr(10).join(all_results[:3])}",
            "أنت مساعد يلخص النتائج بإيجاز واحترافية.",
            400
        )
        if summary:
            await safe_send(bot, chat_id, f"📊 الملخص النهائي:\n\n{summary}")
            memory.add_message(f"ملخص: {summary}")

# ============ 12. النقاش التلقائي ============
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

    await safe_send(bot, chat_id_global, f"💬 بدأ النقاش التلقائي\n\nالموضوع: {current_topic}\n\nاكتب أي رسالة للتدخل في النقاش")

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
                print(f"Discussion Telegram error: {e}")
                break

        await asyncio.sleep(random.randint(20, 45))

# ============ 13. تدخل المستخدم في النقاش ============
async def handle_discussion_input(bot: Bot, chat_id: int, user_text: str):
    conversation_history.append(f"المستخدم: {user_text}")
    agent = random.choice(AGENTS)
    context = "\n".join(conversation_history[-5:])
    search_context = await search_web(user_text)
    search_note = f"\nمعلومة من الإنترنت:\n{search_context[:400]}" if search_context else ""

    response = await groq_generate(
        f"السياق:\n{context}{search_note}\n\nرد على المستخدم: {user_text}",
        f"أنت {agent['name']}، {agent['role']}. رد بشكل مباشر وذكي في 2-3 جمل.",
        200
    )

    if response:
        await safe_send(bot, chat_id, f"{agent['emoji']} {agent['name']} يرد عليك:\n{response}")
        conversation_history.append(f"{agent['name']}: {response}")

# ============ 14. الحلقة الرئيسية ============
async def main():
    global discussion_active, discussion_task, chat_id_global

    bot = Bot(token=TELEGRAM_TOKEN)

    # تجاهل الرسائل القديمة
    last_update_id = None
    try:
        updates = await bot.get_updates(offset=-1, timeout=5)
        if updates:
            last_update_id = updates[-1].update_id + 1
    except Exception:
        pass

    print("🚀 الوكيل الذكي جاهز - LLaMA 3.3 + ذاكرة + تخطيط")

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
                    await safe_send(bot, chat_id, (
                        "🤖 مرحباً! أنا وكيل ذكي متكامل\n\n"
                        "قدراتي:\n"
                        "• أخطط وأنفذ المهام خطوة بخطوة\n"
                        "• أبحث في الإنترنت تلقائياً\n"
                        "• أتذكر كل محادثاتنا\n"
                        "• أتعلم من أخطائي\n\n"
                        "الأوامر:\n"
                        "/agent - وضع الوكيل الذكي\n"
                        "/discuss - وضع النقاش التلقائي\n"
                        "/memory - عرض ذاكرتي\n"
                        "/status - حالة النظام\n"
                        "/clear - مسح الذاكرة\n"
                        "/stop - إيقاف النقاش\n\n"
                        "أرسل أي مهمة وسأنفذها!"
                    ))

                elif text == "/agent":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, (
                        "🧠 وضع الوكيل الذكي مفعّل\n\n"
                        "أرسل أي مهمة وسأخطط لها وأنفذها!\n"
                        "مثال: ابحث عن أفضل 5 لغات برمجة في 2025 وقارن بينها"
                    ))

                elif text == "/discuss":
                    chat_id_global = chat_id
                    discussion_active = True
                    if discussion_task is None or discussion_task.done():
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "⚠️ النقاش يعمل بالفعل!")

                elif text == "/memory":
                    memory = get_memory(chat_id)
                    ctx = memory.get_context()
                    if ctx:
                        await safe_send(bot, chat_id, f"🧠 ذاكرتي عنك:\n\n{ctx[:1000]}")
                    else:
                        await safe_send(bot, chat_id, "🧠 ذاكرتي فارغة حتى الآن.")

                elif text == "/clear":
                    memories.pop(chat_id, None)
                    conversation_history.clear()
                    await safe_send(bot, chat_id, "🗑️ تم مسح الذاكرة كاملاً.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await safe_send(bot, chat_id, "⏹ توقف النقاش.\n\n/discuss لإعادة النقاش\n/agent لتفعيل الوكيل")

                elif text == "/topic":
                    if discussion_active:
                        discussion_active = False
                        if discussion_task and not discussion_task.done():
                            discussion_task.cancel()
                        await asyncio.sleep(1)
                        discussion_active = True
                        discussion_task = asyncio.create_task(run_discussion(bot))
                    else:
                        await safe_send(bot, chat_id, "⚠️ أرسل /discuss أولاً.")

                elif text == "/status":
                    memory = get_memory(chat_id)
                    mode = "نقاش نشط 🟢" if discussion_active else "وكيل ذكي 🔴"
                    await safe_send(bot, chat_id, (
                        f"حالة النظام:\n\n"
                        f"الوضع: {mode}\n"
                        f"المهام المنجزة: {len(memory.tasks)}\n"
                        f"الدروس المتعلمة: {len(memory.learned)}\n"
                        f"النموذج: LLaMA 3.3 70B"
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
