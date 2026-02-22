import asyncio
import random
import os
import google.generativeai as genai
from telegram import Bot
from telegram.error import TelegramError

# ============ الإعدادات الأمنية ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: يرجى التحقق من المتغيرات في Railway")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# ============ إضافة أدوات البحث (Tools) ============
# تم إضافة خاصية google_search_retrieval لتمكين البوت من تصفح الإنترنت
tools = [
    { "google_search_retrieval": {} }
]

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    tools=tools
)

# ============ الوكلاء ============
AGENTS = [
    {"name": "🔍 باحث_أول - أحمد", "role": "خبير البحث في الويب وجلب الأخبار الحقيقية"},
    {"name": "🤖 محلل_بيانات - سارة", "role": "متخصصة في تحليل الأرقام والبيانات التقنية"},
    {"name": "🌐 باحث_ويب - خالد", "role": "خبير في استخدام الـ APIs والمصادر المفتوحة"},
    {"name": "📊 استراتيجي - منى", "role": "خبير التخطيط وربط المعلومات ببعضها"},
    {"name": "⚡ مطور_أتمتة - يوسف", "role": "خبير البرمجة وكتابة الأكواد"}
]

conversation_history = []
discussion_active = False

async def get_ai_response(prompt, use_search=True):
    try:
        # إذا كان الطلب يحتاج بحث، سيقوم Gemini باستخدام جوجل تلقائياً
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        return f"عذراً، واجهت مشكلة في الاتصال بالمصادر الخارجية: {e}"

async def handle_user_command(bot, chat_id, user_text):
    """هذه الدالة تجعل الوكلاء يبحثون وينفذون طلبك"""
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    prompt = f"""المستخدم أرسل أمراً: "{user_text}"
بصفتكم فريق عمل (أحمد، سارة، خالد، منى، يوسف).
إذا كان الطلب يحتاج معلومات حديثة (أسعار، أخبار، طقس)، استخدم أداة البحث في جوجل فوراً.
قدم الإجابة بدقة مع ذكر المصادر إن وجدت، وصغ الرد باسم الوكيل الأنسب."""
    
    response = await get_ai_response(prompt)
    await bot.send_message(chat_id=chat_id, text=f"✅ **تم التنفيذ:**\n\n{response}", parse_mode="Markdown")

async def run_discussion(bot, chat_id):
    global discussion_active
    while discussion_active:
        agent = random.choice(AGENTS)
        history = "\n".join(conversation_history[-3:])
        prompt = f"أنت {agent['name']}. ناقش زملائك باختصار في أتمتة البحث. السياق الحالي: {history}"
        
        response = await get_ai_response(prompt, use_search=False)
        msg = f"*{agent['name']}:*\n{response}"
        
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            conversation_history.append(f"{agent['name']}: {response}")
        except: break
        
        await asyncio.sleep(random.randint(40, 80))

async def main():
    global discussion_active
    bot = Bot(token=TELEGRAM_TOKEN)
    last_update_id = None
    print("🚀 البوت الآن مزود بمحرك بحث جوجل...")

    while True:
        try:
            updates = await bot.get_updates(offset=last_update_id, timeout=20)
            for update in updates:
                if not update.message or not update.message.text: continue
                last_update_id = update.update_id + 1
                
                chat_id = update.message.chat_id
                text = update.message.text

                if text == "/start":
                    discussion_active = True
                    asyncio.create_task(run_discussion(bot, chat_id))
                elif text == "/stop":
                    discussion_active = False
                    await bot.send_message(chat_id=chat_id, text="⏹ توقف النقاش الجانبي. أنا بانتظار أوامرك للبحث.")
                else:
                    await handle_user_command(bot, chat_id, text)

        except Exception as e:
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
