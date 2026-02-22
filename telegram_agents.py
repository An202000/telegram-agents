import asyncio
import random
import os
import google.generativeai as genai
from telegram import Bot
from telegram.error import TelegramError

# ============ 1. الإعدادات الأمنية (من Railway) ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: تأكد من إضافة TELEGRAM_TOKEN و GEMINI_API_KEY في Railway Variables")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# ============ 2. وظيفة إنشاء النموذج بمرونة ============
def create_model(with_tools=True):
    """تحاول إنشاء نموذج مع أدوات البحث، وإذا فشلت تنشئ نموذجاً عادياً"""
    try:
        if with_tools:
            return genai.GenerativeModel(
                model_name='gemini-1.5-flash',
                tools=[{"google_search_retrieval": {}}]
            )
        return genai.GenerativeModel(model_name='gemini-1.5-flash')
    except:
        return genai.GenerativeModel(model_name='gemini-1.5-flash')

# ============ 3. الوكلاء المبرمجون ============
AGENTS = [
    {"name": "🔍 باحث_أول - أحمد", "role": "خبير البحث وجلب المعلومات الحقيقية"},
    {"name": "🤖 محلل_بيانات - سارة", "role": "متخصصة في تحليل الأرقام والبيانات"},
    {"name": "🌐 باحث_ويب - خالد", "role": "خبير المصادر المفتوحة والـ APIs"},
    {"name": "📊 استراتيجي - منى", "role": "خبير ربط المعلومات والتخطيط"},
    {"name": "⚡ مطور_أتمتة - يوسف", "role": "خبير الأكواد والحلول البرمجية"}
]

conversation_history = []
discussion_active = False

# ============ 4. وظيفة التوليد الذكي ============
async def get_ai_response(prompt):
    """تحاول التوليد مع البحث، وإذا فشلت تولد رداً عادياً فوراً"""
    try:
        # المحاولة الأولى: مع محرك بحث جوجل
        model = create_model(with_tools=True)
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Search failed, using fallback: {e}")
        try:
            # المحاولة الثانية: رد ذكاء اصطناعي مباشر (بدون إنترنت)
            model = create_model(with_tools=False)
            response = await asyncio.to_thread(model.generate_content, prompt)
            return response.text.strip()
        except Exception as e2:
            return f"عذراً يا عنتر، واجهت مشكلة تقنية في الاتصال: {str(e2)[:50]}"

# ============ 5. معالجة أوامر المستخدم ============
async def handle_user_command(bot, chat_id, user_text):
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    prompt = f"""المستخدم أرسل طلباً: "{user_text}".
بصفتكم فريق وكلاء (أحمد، سارة، خالد، منى، يوسف)، 
قوموا بتنفيذ الطلب أو الإجابة عليه بدقة بلسان الوكيل الأنسب. 
إذا كان الطلب يحتاج معلومات حديثة، استخدموا البحث في جوجل."""
    
    response = await get_ai_response(prompt)
    await bot.send_message(chat_id=chat_id, text=f"✅ **تم التنفيذ:**\n\n{response}", parse_mode="Markdown")

# ============ 6. إدارة النقاش التلقائي ============
async def run_discussion(bot, chat_id):
    global discussion_active
    while discussion_active:
        agent = random.choice(AGENTS)
        history = "\n".join(conversation_history[-3:])
        prompt = f"أنت {agent['name']}. شارك في النقاش حول أتمتة البحث بجملة واحدة ذكية. السياق: {history}"
        
        response = await get_ai_response(prompt)
        msg = f"*{agent['name']}:*\n{response}"
        
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            conversation_history.append(f"{agent['name']}: {response}")
            if len(conversation_history) > 10: conversation_history.pop(0)
        except: break
        
        await asyncio.sleep(random.randint(60, 120))

# ============ 7. الحلقة الرئيسية للبوت ============
async def main():
    global discussion_active
    bot = Bot(token=TELEGRAM_TOKEN)
    last_update_id = None
    print("🚀 البوت يعمل الآن بنظام التوليد المزدوج...")

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
                    await bot.send_message(chat_id=chat_id, text="⏹ توقف النقاش الجانبي. بانتظار أوامرك.")
                elif text == "/status":
                    await bot.send_message(chat_id=chat_id, text="🟢 البوت متصل وجاهز للعمل.")
                else:
                    # أي رسالة عادية تعتبر أمراً للتنفيذ
                    await handle_user_command(bot, chat_id, text)

        except Exception as e:
            print(f"Main loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
