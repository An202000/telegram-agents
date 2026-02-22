import asyncio
import random
import os
from groq import Groq
from telegram import Bot
from telegram.error import TelegramError
from duckduckgo_search import DDGS

# ============ 1. الإعدادات الأمنية (من Railway) ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise EnvironmentError("❌ خطأ: تأكد من إضافة TELEGRAM_TOKEN و GROQ_API_KEY في Railway Variables")

client = Groq(api_key=GROQ_API_KEY)

# ============ 2. الوكلاء المبرمجون ============
AGENTS = [
    {"name": "🔍 باحث_أول - أحمد", "role": "خبير البحث وجلب المعلومات الحقيقية"},
    {"name": "🤖 محلل_بيانات - سارة", "role": "متخصصة في تحليل الأرقام والبيانات"},
    {"name": "🌐 باحث_ويب - خالد", "role": "خبير المصادر المفتوحة والـ APIs"},
    {"name": "📊 استراتيجي - منى", "role": "خبير ربط المعلومات والتخطيط"},
    {"name": "⚡ مطور_أتمتة - يوسف", "role": "خبير الأكواد والحلول البرمجية"}
]

conversation_histories: dict[int, list[str]] = {}
discussion_active = False
discussion_task: asyncio.Task | None = None

# ============ 3. وظيفة Groq للتوليد ============
async def groq_generate(prompt: str, system: str = "أنت مساعد ذكي ومفيد يتحدث العربية.") -> str:
    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1024,
                temperature=0.7
            )
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return f"عذراً، واجهت مشكلة تقنية: {str(e)[:100]}"

# ============ 4. البحث عبر DuckDuckGo ============
async def search_web(query: str, max_results: int = 5) -> str:
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            return ""
        formatted = ""
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            formatted += f"{i}. {title}\n{body}\nالمصدر: {href}\n\n"
        return formatted.strip()
    except Exception as e:
        print(f"DuckDuckGo error: {e}")
        return ""

# ============ 5. وظيفة التوليد الذكي مع البحث ============
async def get_ai_response(prompt: str, use_search: bool = True) -> str:
    search_context = ""

    if use_search:
        # استخراج كلمات البحث
        search_query = await groq_generate(
            f"استخرج كلمات البحث المناسبة من هذا الطلب (جملة قصيرة فقط بدون شرح): {prompt}",
            system="أنت مساعد يستخرج كلمات البحث فقط."
        )
        print(f"🔍 البحث عن: {search_query}")
        search_context = await search_web(search_query)

    if search_context:
        final_prompt = f"""استخدم نتائج البحث التالية للإجابة على الطلب:

نتائج البحث:
{search_context}

الطلب:
{prompt}

أجب بشكل واضح ومفيد بالعربية."""
    else:
        final_prompt = prompt

    return await groq_generate(final_prompt)

# ============ 6. معالجة أوامر المستخدم ============
async def handle_user_command(bot: Bot, chat_id: int, user_text: str):
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    searching_msg = await bot.send_message(chat_id=chat_id, text="🔍 جاري البحث في الإنترنت...")

    prompt = f"""المستخدم أرسل: "{user_text}"
بصفتك فريق وكلاء (أحمد، سارة، خالد، منى، يوسف)،
أجب بدقة بلسان الوكيل الأنسب مع الاستفادة من نتائج البحث."""

    response = await get_ai_response(prompt, use_search=True)

    try:
        await bot.delete_message(chat_id=chat_id, message_id=searching_msg.message_id)
    except Exception:
        pass

    await bot.send_message(chat_id=chat_id, text=f"✅ **تم التنفيذ:**\n\n{response}", parse_mode="Markdown")

# ============ 7. إدارة النقاش التلقائي ============
async def run_discussion(bot: Bot, chat_id: int):
    global discussion_active
    while discussion_active:
        agent = random.choice(AGENTS)
        history = conversation_histories.get(chat_id, [])
        history_text = "\n".join(history[-3:])

        prompt = f"أنت {agent['name']}، {agent['role']}. شارك في النقاش حول أتمتة البحث بجملة واحدة ذكية. السياق: {history_text}"
        response = await get_ai_response(prompt, use_search=False)
        msg = f"*{agent['name']}:*\n{response}"

        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            if chat_id not in conversation_histories:
                conversation_histories[chat_id] = []
            conversation_histories[chat_id].append(f"{agent['name']}: {response}")
            if len(conversation_histories[chat_id]) > 10:
                conversation_histories[chat_id].pop(0)
        except TelegramError as e:
            print(f"Telegram error: {e}")
            break
        except Exception as e:
            print(f"Discussion error: {e}")
            break

        await asyncio.sleep(random.randint(60, 120))

# ============ 8. الحلقة الرئيسية ============
async def main():
    global discussion_active, discussion_task
    bot = Bot(token=TELEGRAM_TOKEN)

    last_update_id = None
    try:
        updates = await bot.get_updates(offset=-1, timeout=5)
        if updates:
            last_update_id = updates[-1].update_id + 1
    except Exception:
        pass

    print("🚀 البوت يعمل مع Groq LLaMA 3.3 + DuckDuckGo...")

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
                    discussion_active = True
                    if discussion_task is None or discussion_task.done():
                        discussion_task = asyncio.create_task(run_discussion(bot, chat_id))
                        await bot.send_message(chat_id=chat_id, text=(
                            "🤖 *مرحباً! البوت جاهز*\n\n"
                            "🧠 النموذج: LLaMA 3.3 70B\n"
                            "🌐 البحث: DuckDuckGo\n\n"
                            "أرسل أي سؤال وسأبحث عنه فوراً!"
                        ), parse_mode="Markdown")
                    else:
                        await bot.send_message(chat_id=chat_id, text="⚠️ النقاش يعمل بالفعل.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await bot.send_message(chat_id=chat_id, text="⏹ توقف النقاش. بانتظار أوامرك.")

                elif text == "/status":
                    status = "🟢 نشط" if discussion_active else "🔴 متوقف"
                    await bot.send_message(chat_id=chat_id, text=(
                        f"*حالة البوت:*\n"
                        f"النقاش: {status}\n"
                        f"🧠 النموذج: LLaMA 3.3 70B\n"
                        f"🌐 البحث: DuckDuckGo مفعّل"
                    ), parse_mode="Markdown")

                elif text == "/clear":
                    conversation_histories.pop(chat_id, None)
                    await bot.send_message(chat_id=chat_id, text="🗑️ تم مسح تاريخ المحادثة.")

                elif text == "/help":
                    await bot.send_message(chat_id=chat_id, text=(
                        "📖 *الأوامر المتاحة:*\n\n"
                        "/start - تشغيل البوت والنقاش\n"
                        "/stop - إيقاف النقاش\n"
                        "/status - حالة البوت\n"
                        "/clear - مسح المحادثة\n"
                        "/help - هذه القائمة\n\n"
                        "💡 أرسل أي سؤال وسيبحث البوت تلقائياً 🌐"
                    ), parse_mode="Markdown")

                else:
                    await handle_user_command(bot, chat_id, text)

        except TelegramError as e:
            print(f"Telegram error: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
