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

# إعداد المكتبة
genai.configure(api_key=GEMINI_API_KEY)

# ============ إعداد النموذج مع البحث (المسار الصحيح) ============
# ملاحظة: تم تعديل طريقة تعريف الأداة لتجنب خطأ 404
model = genai.GenerativeModel(
    model_name='models/gemini-1.5-flash', # استخدام الاسم الكامل للموديل
    tools=[{"google_search_retrieval": {}}]
)

# ============ الوكلاء ============
AGENTS = [
    {"name": "🔍 باحث_أول - أحمد", "role": "خبير البحث في الويب"},
    {"name": "🤖 محلل_بيانات - سارة", "role": "خبير التحليل"},
    {"name": "🌐 باحث_ويب - خالد", "role": "خبير الـ APIs"},
    {"name": "📊 استراتيجي - منى", "role": "خبير التخطيط"},
    {"name": "⚡ مطور_أتمتة - يوسف", "role": "خبير الأكواد"}
]

conversation_history = []
discussion_active = False

async def get_ai_response(prompt):
    try:
        # التوليد مع تفعيل البحث
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        # إذا فشل البحث، نحاول التوليد العادي كخطة بديلة
        print(f"Search Error: {e}")
        try:
            fallback_model = genai.GenerativeModel('models/gemini-1.5-flash')
            response = await asyncio.to_thread(fallback_model.generate_content, prompt)
            return response.text.strip()
        except:
            return "عذراً، أواجه صعوبة في الوصول للمعلومات حالياً."

async def handle_user_command(bot, chat_id, user_text):
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    prompt = f"""أنت فريق وكلاء ذكاء اصطناعي. المستخدم أرسل: "{user_text}".
استخدم البحث في جوجل إذا كان الطلب يتطلب معلومات حديثة.
أجب بلسان الوكيل الأنسب للمهمة وكن دقيقاً جداً."""
    
    response = await get_ai_response(prompt)
    await bot.send_message(chat_id=chat_id, text=f"✅ **تم التنفيذ:**\n\n{response}", parse_mode="Markdown")

async def run_discussion(bot, chat_id):
    global discussion_active
    while discussion_active:
        agent = random.choice(AGENTS)
        prompt = f"أنت {agent['name']}. أعطِ فكرة مختصرة عن أتمتة البحث."
        response = await get_ai_response(prompt)
        try:
            await bot.send_message(chat_id=chat_id, text=f"*{agent['name']}:*\n{response}", parse_mode="Markdown")
        except: break
        await asyncio.sleep(random.randint(60, 120))

async def main():
    global discussion_active
    bot = Bot(token=TELEGRAM_TOKEN)
    last_update_id = None
    print("🚀 البوت يعمل الآن مع محرك بحث مصحح...")

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
                    await bot.send_message(chat_id=chat_id, text="⏹ توقف النقاش.")
                else:
                    await handle_user_command(bot, chat_id, text)
        except Exception as e:
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
