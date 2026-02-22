import asyncio
import random
import os
import google.generativeai as genai
from telegram import Bot
from telegram.error import TelegramError
from duckduckgo_search import DDGS

# ============ 1. الإعدادات الأمنية (من Railway) ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise EnvironmentError("❌ خطأ: تأكد من إضافة TELEGRAM_TOKEN و GEMINI_API_KEY في Railway Variables")

genai.configure(api_key=GEMINI_API_KEY)

# ============ 2. وظيفة إنشاء النموذج (بدون tools خارجية) ============
def create_model():
    return genai.GenerativeModel(model_name='gemini-2.0-flash')

# ============ 3. البحث الحقيقي عبر DuckDuckGo ============
async def search_web(query: str, max_results: int = 5) -> str:
    """يبحث في الإنترنت عبر DuckDuckGo ويرجع النتائج كنص"""
    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=max_results))
        )
        if not results:
            return "لم يتم العثور على نتائج."

        formatted = ""
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            formatted += f"{i}. {title}\n{body}\nالمصدر: {href}\n\n"
        return formatted.strip()
    except Exception as e:
        print(f"DuckDuckGo search error: {e}")
        return ""

# ============ 4. الوكلاء المبرمجون ============
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

# ============ 5. وظيفة التوليد الذكي مع البحث الحقيقي ============
async def get_ai_response(prompt: str, use_search: bool = True) -> str:
    """
    1) يبحث في DuckDuckGo إذا احتاج
    2) يعطي النتائج لـ Gemini ليولد رداً ذكياً
    """
    search_context = ""

    if use_search:
        # استخراج موضوع البحث من الـ prompt
        search_query_prompt = f"استخرج كلمات البحث المناسبة من هذا الطلب بالعربية أو الإنجليزية (جملة قصيرة فقط بدون شرح): {prompt}"
        try:
            model = create_model()
            query_response = await asyncio.to_thread(model.generate_content, search_query_prompt)
            search_query = query_response.text.strip()
            print(f"🔍 جاري البحث عن: {search_query}")

            search_context = await search_web(search_query)
            if search_context:
                print(f"✅ تم جلب {len(search_context)} حرف من نتائج البحث")
        except Exception as e:
            print(f"Failed to generate search query: {e}")

    # بناء الـ prompt النهائي مع نتائج البحث
    if search_context:
        final_prompt = f"""أنت مساعد ذكي. استخدم نتائج البحث التالية للإجابة على الطلب.

نتائج البحث من الإنترنت:
{search_context}

الطلب:
{prompt}

أجب بشكل واضح ومفيد بناءً على المعلومات أعلاه."""
    else:
        final_prompt = prompt

    try:
        model = create_model()
        response = await asyncio.to_thread(model.generate_content, final_prompt)
        return response.text.strip()
    except Exception as e:
        return f"عذراً، واجهت مشكلة تقنية: {str(e)[:100]}"

# ============ 6. معالجة أوامر المستخدم ============
async def handle_user_command(bot: Bot, chat_id: int, user_text: str):
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    # إشعار المستخدم أن البحث جارٍ
    searching_msg = await bot.send_message(chat_id=chat_id, text="🔍 جاري البحث في الإنترنت...")

    prompt = f"""المستخدم أرسل طلباً: "{user_text}".
بصفتكم فريق وكلاء (أحمد، سارة، خالد، منى، يوسف)، 
قوموا بتنفيذ الطلب أو الإجابة عليه بدقة بلسان الوكيل الأنسب،
مع الاستفادة من نتائج البحث المقدمة."""

    response = await get_ai_response(prompt, use_search=True)

    # حذف رسالة "جاري البحث" وإرسال الرد
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
        prompt = f"أنت {agent['name']}. شارك في النقاش حول أتمتة البحث بجملة واحدة ذكية. السياق: {history_text}"

        # النقاش لا يحتاج بحث في الإنترنت دائماً
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
            print(f"Telegram error in discussion: {e}")
            break
        except Exception as e:
            print(f"Unexpected error in discussion: {e}")
            break

        await asyncio.sleep(random.randint(60, 120))

# ============ 8. الحلقة الرئيسية للبوت ============
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

    print("🚀 البوت يعمل الآن مع بحث DuckDuckGo الحقيقي...")

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
                        await bot.send_message(chat_id=chat_id, text="▶️ بدأ النقاش التلقائي!\n\nأرسل أي سؤال وسأبحث عنه في الإنترنت 🌐")
                    else:
                        await bot.send_message(chat_id=chat_id, text="⚠️ النقاش يعمل بالفعل.")

                elif text == "/stop":
                    discussion_active = False
                    if discussion_task and not discussion_task.done():
                        discussion_task.cancel()
                    await bot.send_message(chat_id=chat_id, text="⏹ توقف النقاش الجانبي. بانتظار أوامرك.")

                elif text == "/status":
                    status = "🟢 النقاش نشط" if discussion_active else "🔴 النقاش متوقف"
                    await bot.send_message(chat_id=chat_id, text=f"{status}\n✅ البوت متصل وجاهز للعمل.\n🌐 البحث عبر DuckDuckGo مفعّل.")

                elif text == "/clear":
                    conversation_histories.pop(chat_id, None)
                    await bot.send_message(chat_id=chat_id, text="🗑️ تم مسح تاريخ المحادثة.")

                elif text == "/help":
                    help_text = (
                        "📖 *الأوامر المتاحة:*\n\n"
                        "/start - بدء النقاش التلقائي\n"
                        "/stop - إيقاف النقاش\n"
                        "/status - حالة البوت\n"
                        "/clear - مسح تاريخ المحادثة\n"
                        "/help - عرض هذه القائمة\n\n"
                        "💡 أرسل أي سؤال وسيبحث البوت عنه في الإنترنت تلقائياً 🌐"
                    )
                    await bot.send_message(chat_id=chat_id, text=help_text, parse_mode="Markdown")

                else:
                    await handle_user_command(bot, chat_id, text)

        except TelegramError as e:
            print(f"Telegram error in main loop: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
