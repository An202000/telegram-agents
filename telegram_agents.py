import asyncio
import random
import os
import google.generativeai as genai
from telegram import Bot
from telegram.error import TelegramError

# ============ الإعدادات الأمنية (Railway) ============
# جلب الإعدادات من متغيرات البيئة لضمان عدم تسريب المفاتيح
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# التحقق من وجود المفاتيح قبل التشغيل
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ خطأ: يرجى إضافة TELEGRAM_TOKEN و GEMINI_API_KEY في إعدادات Railway (Variables)")
    exit(1)

# إعداد نموذج Gemini
genai.configure(api_key=GEMINI_API_KEY)
# تصحيح: إضافة علامات التنصيص حول اسم النموذج
model = genai.GenerativeModel("gemini-1.5-flash") 

# ============ الوكلاء الخمسة ============
AGENTS = [
    {
        "name": "🔍 باحث_أول - أحمد",
        "role": "خبير في البحث عن مصادر المعلومات والبيانات المفتوحة",
        "personality": "دقيق ومنهجي، يحب الأدلة والإحصاءات"
    },
    {
        "name": "🤖 محلل_بيانات - سارة",
        "role": "متخصصة في تحليل البيانات وأتمتة جمعها",
        "personality": "تقنية ومبدعة، تقترح حلولاً برمجية"
    },
    {
        "name": "🌐 باحث_ويب - خالد",
        "role": "خبير في استخراج المعلومات من الإنترنت والـ APIs",
        "personality": "عملي ومباشر، يركز على النتائج السريعة"
    },
    {
        "name": "📊 استراتيجي - منى",
        "role": "متخصصة في استراتيجيات البحث وتنظيم المعلومات",
        "personality": "تفكر بشكل كبير، ترى الصورة الكاملة"
    },
    {
        "name": "⚡ مطور_أتمتة - يوسف",
        "role": "مطور متخصص في بناء أدوات أتمتة البحث",
        "personality": "مبتكر وحماسي، يقترح تقنيات جديدة"
    }
]

conversation_history = []
current_agent_index = 0
discussion_active = False # متغير للتحكم في حالة النقاش

def get_next_agent():
    global current_agent_index
    agent = AGENTS[current_agent_index]
    current_agent_index = (current_agent_index + 1) % len(AGENTS)
    return agent

async def generate_response(agent, topic, last_messages):
    history_text = "\n".join(last_messages[-6:]) if last_messages else "بداية النقاش"
    
    prompt = f"""أنت {agent['name']}. 
دورك: {agent['role']}. شخصيتك: {agent['personality']}.
الموضوع: أتمتة مهام البحث وجلب المعلومات.

السياق الحالي:
{history_text}

اكتب رداً قصيراً (2-3 جمل) بالعربية الفصحى، يضيف قيمة للنقاش أو يسأل سؤالاً ذكياً.
لا تكرر كلام الآخرين."""

    try:
        # استخدام asyncio لتجنب حظر البوت أثناء التوليد
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        print(f"خطأ في AI: {e}")
        return "أعتقد أننا بحاجة للتركيز أكثر على الأدوات التقنية المتاحة حالياً."

async def run_discussion(bot, chat_id):
    global conversation_history, discussion_active
    discussion_active = True
    
    await bot.send_message(
        chat_id=chat_id,
        text="🚀 *بدأ النقاش بين الوكلاء الخمسة!*\n\nاكتب /stop لإيقاف النقاش في أي وقت.",
        parse_mode="Markdown"
    )
    
    while discussion_active:
        agent = get_next_agent()
        response = await generate_response(agent, "أتمتة البحث", conversation_history)
        
        msg = f"*{agent['name']}:*\n{response}"
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            conversation_history.append(f"{agent['name']}: {response}")
            
            # إبقاء الذاكرة خفيفة
            if len(conversation_history) > 15:
                conversation_history.pop(0)
                
        except TelegramError as e:
            print(f"Telegram Error: {e}")
            break
            
        # مدة الانتظار بين ردود الوكلاء (يمكنك تعديلها)
        await asyncio.sleep(random.randint(20, 40))

async def main():
    global discussion_active
    bot = Bot(token=TELEGRAM_TOKEN)
    print("✅ البوت يعمل بنجاح... في انتظار الأوامر.")
    
    last_update_id = None
    
    while True:
        try:
            updates = await bot.get_updates(offset=last_update_id, timeout=20)
            for update in updates:
                last_update_id = update.update_id + 1
                if not update.message or not update.message.text:
                    continue
                
                chat_id = update.message.chat_id
                text = update.message.text

                if text == "/start":
                    if not discussion_active:
                        asyncio.create_task(run_discussion(bot, chat_id))
                    else:
                        await bot.send_message(chat_id=chat_id, text="⚠️ النقاش جارٍ بالفعل!")
                
                elif text == "/stop":
                    discussion_active = False
                    await bot.send_message(chat_id=chat_id, text="⏹ تم إيقاف النقاش.")
                
                elif text == "/status":
                    status = "يعمل 🟢" if discussion_active else "متوقف 🔴"
                    await bot.send_message(chat_id=chat_id, text=f"وضع البوت: {status}")

        except Exception as e:
            print(f"Error in main loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
