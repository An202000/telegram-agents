import asyncio
import random
import google.generativeai as genai
from telegram import Bot
from telegram.error import TelegramError

# ============ الإعدادات ============
TELEGRAM_TOKEN = "8317346256:AAFYz4Aw_5cvth-cg-UoUW1Xwg2-pkJ1D9k"
GEMINI_API_KEY = "AIzaSyDU41B-yE3yEn1liqPQJgIxHvv8Ylmrgug"
CHAT_ID = None  # سيتم تحديده تلقائياً

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

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

def get_next_agent():
    global current_agent_index
    agent = AGENTS[current_agent_index]
    current_agent_index = (current_agent_index + 1) % len(AGENTS)
    return agent

async def generate_response(agent, topic, last_messages):
    history_text = "\n".join(last_messages[-6:]) if last_messages else "بداية النقاش"
    
    prompt = f"""أنت {agent['name']}.
دورك: {agent['role']}
شخصيتك: {agent['personality']}

الموضوع الرئيسي: أتمتة مهام البحث وجلب المعلومات

آخر ما قيله الزملاء:
{history_text}

اكتب ردك في النقاش (جملتين أو ثلاث فقط، بشكل طبيعي وحواري، بالعربية).
لا تكرر ما قيل، أضف رأياً أو فكرة جديدة أو اعتراضاً أو سؤالاً."""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[خطأ في التوليد: {e}]"

async def run_discussion(bot, chat_id):
    global conversation_history
    
    # رسالة البداية
    await bot.send_message(
        chat_id=chat_id,
        text="🚀 *بدأ النقاش بين الوكلاء الخمسة حول أتمتة مهام البحث!*\n\nاكتب /stop لإيقاف النقاش",
        parse_mode="Markdown"
    )
    
    await asyncio.sleep(2)
    
    # رسالة افتتاحية من أول وكيل
    first_agent = AGENTS[0]
    opener = "مرحباً بالجميع! دعونا نناقش كيف يمكننا أتمتة مهام البحث وجلب المعلومات بشكل فعال. ما هي أفضل الأدوات والاستراتيجيات برأيكم؟"
    
    msg = f"*{first_agent['name']}:*\n{opener}"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    conversation_history.append(f"{first_agent['name']}: {opener}")
    
    round_num = 0
    while True:
        round_num += 1
        
        # كل 10 جولات، أضف موضوعاً جديداً
        if round_num % 10 == 0:
            topics = [
                "ما هي أفضل APIs المجانية للبحث؟",
                "كيف نتعامل مع الـ Rate Limiting؟",
                "ما دور الذكاء الاصطناعي في تصنيف المعلومات؟",
                "كيف نضمن جودة البيانات المجمعة؟"
            ]
            new_topic = random.choice(topics)
            await bot.send_message(
                chat_id=chat_id,
                text=f"💡 *موضوع جديد للنقاش:* {new_topic}",
                parse_mode="Markdown"
            )
        
        # اختر الوكيل التالي
        agent = get_next_agent()
        
        # توليد الرد
        response = await generate_response(agent, "أتمتة البحث", conversation_history)
        
        # أرسل الرسالة
        msg = f"*{agent['name']}:*\n{response}"
        try:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            print(f"خطأ في الإرسال: {e}")
            break
        
        # احفظ في السجل
        conversation_history.append(f"{agent['name']}: {response}")
        if len(conversation_history) > 20:
            conversation_history = conversation_history[-20:]
        
        # انتظر بين الرسائل (30-60 ثانية)
        delay = random.randint(30, 60)
        await asyncio.sleep(delay)

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    
    print("✅ البوت يعمل... في انتظار رسالة /start")
    print("أرسل /start في محادثة البوت لبدء النقاش")
    
    last_update_id = None
    
    while True:
        try:
            updates = await bot.get_updates(
                offset=last_update_id,
                timeout=10,
                allowed_updates=["message"]
            )
            
            for update in updates:
                last_update_id = update.update_id + 1
                
                if update.message and update.message.text:
                    chat_id = update.message.chat_id
                    text = update.message.text
                    
                    if text == "/start":
                        await run_discussion(bot, chat_id)
                    elif text == "/stop":
                        await bot.send_message(
                            chat_id=chat_id,
                            text="⏹ تم إيقاف النقاش. أرسل /start لإعادة البدء."
                        )
                        conversation_history.clear()
                    elif text == "/status":
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ البوت يعمل\n📝 عدد الرسائل في الذاكرة: {len(conversation_history)}"
                        )
        
        except TelegramError as e:
            print(f"خطأ تيليغرام: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"خطأ عام: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
