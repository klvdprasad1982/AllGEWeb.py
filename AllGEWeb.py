import requests
import time
import telebot
import feedparser
from datetime import datetime, timedelta
from google import genai
from deep_translator import GoogleTranslator
import sys
import threading
from queue import Queue
import os
import pytz
import gc
from groq import Groq
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- టైమ్ జోన్ సెటప్ ---
IST = pytz.timezone("Asia/Kolkata")
US = pytz.timezone("US/Eastern")
EU = pytz.timezone("Europe/Berlin")
JP = pytz.timezone("Asia/Tokyo")
HK = pytz.timezone("Asia/Hong_Kong")

sys.stdout.reconfigure(encoding='utf-8')

# --- API Keys & IDs (Render లో Env Variables సెట్ చేయండి) ---
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FINNHUB_KEY = os.getenv("FINNHUB_KEY")
GROQ_KEY = os.getenv("GROQ_KEY")


if not all([TOKEN, CHAT_ID, GEMINI_API_KEY, FINNHUB_KEY, GROQ_KEY]):
    print("❌ Error: కొన్ని API Keys సెట్ చేయబడలేదు! Render Env Variables చెక్ చేయండి.")

bot = telebot.TeleBot(TOKEN)

def safe_send(msg, chat_id=CHAT_ID, parse_mode="HTML", disable_preview=False):
    MAX_LENGTH = 4000  # టెలిగ్రామ్ లిమిట్ ప్రకారం
    
    # మెసేజ్ 4000 క్యారెక్టర్ల కంటే ఎక్కువ ఉంటే ముక్కలుగా విడగొడుతుంది
    if len(msg) > MAX_LENGTH:
        parts = [msg[i:i+MAX_LENGTH] for i in range(0, len(msg), MAX_LENGTH)]
    else:
        parts = [msg]

    for part in parts:
        for i in range(3): # 3 సార్లు ప్రయత్నిస్తుంది
            try:
                bot.send_message(
                    chat_id,
                    part,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview
                )
                break
            except Exception as e:
                print(f"Retry {i+1}: {e}")
                time.sleep(3)

# Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

def safe_gemini(prompt):
    for i in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"Gemini Retry {i+1}: {e}")
            time.sleep(5)
    return "AI అందుబాటులో లేదు"

groq_client = Groq(api_key=GROQ_KEY)

ai_queue = Queue()
HEADERS = {"User-Agent": "Mozilla/5.0"}
sent_news = set()
sent_alerts = {}
sudden_move_sent = {}
gap_alert_sent = {}
collected_news = []
last_sent_results = []
last_reset_date = datetime.now(IST).date()

# --- మార్కెట్ డేటా & టైమింగ్స్ ---
news_feeds = [
    "https://www.forexlive.com/rss",
    "https://www.investing.com/rss/news_1.rss",
    "https://www.investing.com/rss/news_301.rss",
]

TIMINGS = {
    "GIFT Nifty": ("06:30", "02:45"), # IST ప్రకారం రెండు సెషన్లు కలిపి
    "Nikkei (Japan)": ("05:30", "11:30"),
    "Hang Seng (HK)": ("06:45", "13:30"),
    "DAX (Germany)": ("12:30", "21:00"),
    "FTSE (UK)": ("12:30", "21:00"),
    "Dow Jones (US)": ("19:00", "01:30"),
    "Nasdaq (US)": ("19:00", "01:30"),
    "S&P 500 (US)": ("19:00", "01:30"),
    "Gold (Commodity)": ("04:30", "03:30"),
    "Silver (Commodity)": ("04:30", "03:30"),
    "Brent Oil": ("05:30", "03:30"),
    "WTI Crude (US Oil)": ("03:30", "02:30"), # US Oil సమయం
    "US 10Y Yield": ("18:30", "03:30"),
    "Bitcoin (Daily)": ("05:30", "05:29"),
}

symbols = {
    "GIFT Nifty": "^NSEI", # లేదా నిఫ్టీ ఫ్యూచర్స్ కోసం "NIFTY_F1" (Yahoo Finance ని బట్టి)
    "Dow Jones (US)": "^DJI",
    "Nasdaq (US)": "^IXIC",
    "S&P 500 (US)": "^GSPC",
    "Nikkei (Japan)": "^N225",
    "Hang Seng (HK)": "^HSI",
    "DAX (Germany)": "^GDAXI",
    "FTSE (UK)": "^FTSE",
    "Gold (Commodity)": "GC=F",
    "Silver (Commodity)": "SI=F",
    "Brent Oil": "BZ=F",
    "WTI Crude (US Oil)": "CL=F", # US Oil ఇక్కడ యాడ్ చేశాను
    "Bitcoin (Daily)": "BTC-USD",
    "US 10Y Yield": "^TNX",
}

IMPORTANT_EVENTS = [
    "GDP Growth Rate", "India Fiscal Year GDP Growth", "Inflation Rate YoY", 
    "WPI Inflation YoY", "Industrial Production YoY", "RBI Interest Rate Decision", 
    "Fed Interest Rate Decision", "BoJ Interest Rate Decision", "ECB Interest Rate Decision", 
    "FOMC Minutes", "Non Farm Payrolls", "Initial Jobless Claims", "Retail Sales YoY", 
    "NBS Manufacturing PMI", "Union Budget", "Election Results", "Housing Starts", 
    "New Home Sales", "Existing Home Sales", "Market Holiday", "CPI", "Consumer Price Index", "Producer Price Index", "Unemployment Rate", "Core PCE Price Index", "Personal Income",  
    
] 

def log(msg):
    print(f"🚀 [{datetime.now(IST).strftime('%H:%M:%S')}] {msg}")

# --- AI & Translation Functions ---
def translate_to_telugu(text):
    try:
        return GoogleTranslator(source='auto', target='te').translate(text)
    except:
        return text 

def get_groq_analysis(prompt_text):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            chat_completion = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": f"మీరు ఒక స్టాక్ మార్కెట్ నిపుణుడు. ఈ డేటాను చదివి, చంటి గారికి అర్థమయ్యేలా 2-3 సులభమైన తెలుగు వాక్యాల్లో విశ్లేషణ ఇవ్వండి. మార్కెట్ పెరుగుతుందా లేదా తగ్గుతుందా అని చెప్పండి: {prompt_text}"}],
                model="llama-3.3-70b-versatile",
            )
            return chat_completion.choices[0].message.content 
        except Exception as e:
            log(f"Groq AI Error (Attempt {attempt+1}): {e}")
            time.sleep(5)
    return "క్షమించండి చంటి గారు, AI విశ్లేషణ ప్రస్తుతం అందుబాటులో లేదు." 

# --- Market Status & Data Functions ---
def is_market_open(name):
    now_ist = datetime.now(IST)
    if "Bitcoin" in name or "BTC" in name: 
        return "🟢"
    
    # GIFT Nifty & Oils కోసం 
    if any(x in name for x in ["GIFT Nifty", "WTI Crude", "Brent", "Gold", "Silver"]):
        return "🟢" # ఇవి దాదాపు రోజంతా నడుస్తాయి కాబట్టి 🟢 ఉంచాను

    mapping = {
        "Nikkei": (JP, "09:00", "15:00"),
        "Hang Seng": (HK, "09:30", "16:00"),
        "DAX": (EU, "09:00", "17:30"),
        "FTSE": (EU, "08:00", "16:30"),
        "Dow": (US, "09:30", "16:00"), 
        "Nasdaq": (US, "09:30", "16:00"), 
        "S&P": (US, "09:30", "16:00"),
        "10Y": (US, "08:00", "17:00")
    }
    # ... మిగతా కోడ్ యధాతథం ...
    
    for key, (tz, start, end) in mapping.items():
        if key in name:
            now_local = now_ist.astimezone(tz).time()
            start_time = datetime.strptime(start, "%H:%M").time()
            end_time = datetime.strptime(end, "%H:%M").time()
            if start_time <= now_local < end_time:
                return "🟢"
            return "🔴"
            
    if any(x in name for x in ["Gold", "Silver", "Brent"]):
        now_local = now_ist.astimezone(US).time()
        if now_local >= datetime.strptime("18:00", "%H:%M").time() or now_local < datetime.strptime("17:00", "%H:%M").time():
            return "🟢"
        return "🔴"
    return "🔴" 

def get_data(symbol):
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", headers=HEADERS, timeout=10)
        result = r.json()["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if (price is None or price == 0) and "indicators" in result:
            closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c]
            if closes: 
                price = closes[-1]
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
        return price, prev_close
    except: 
        return None, None 

# --- Gap Alert Logic ---
def check_gap_alert(name, price, prev_close, current_date):
    if not price or not prev_close: 
        return
    gap_percent = ((price - prev_close) / prev_close) * 100
    gap_key = f"{name}_{current_date}_gap"
    if gap_key not in gap_alert_sent and abs(gap_percent) >= 1.0:
        direction = "📈 **GAP UP**" if gap_percent > 0 else "📉 **GAP DOWN**"
        safe_send(f"🚨 <b>GAP ALERT!</b>\n\n{name}\n{direction}: {gap_percent:+.2f}%\nCurrent: {price:.2f} | Prev Close: {prev_close:.2f}")
        gap_alert_sent[gap_key] = True 

# --- Economic Calendar Functions ---
def fetch_economic_calendar(days=1):
    try:
        now_ist = datetime.now(IST)
        start_date = now_ist.strftime('%Y-%m-%d')
        end_date = (now_ist + timedelta(days=days)).strftime('%Y-%m-%d')
        
        url = f"https://finnhub.io/api/v1/calendar/economic?from={start_date}&to={end_date}&token={FINNHUB_KEY}"
        events = requests.get(url, timeout=20).json().get("economicCalendar", [])
        
        # సమయం ప్రకారం ఈవెంట్లను అమర్చడం
        events.sort(key=lambda x: x.get("time", ""))
        
        report = ""
        found_any = False
        targets = ["IN", "US", "JP", "CN", "EU"]

        for item in events:
            event_name = item.get("event", "")
            country = item.get("country", "")
            
            if any(t in country for t in targets):
                # వారం రిపోర్ట్ (days > 1) అయితేనే IMPORTANT_EVENTS ఫిల్టర్ పనిచేస్తుంది
                if days > 1:
                    if not any(imp in event_name for imp in IMPORTANT_EVENTS):
                        continue
                
                event_time_raw = item.get("time", "")
                if not event_time_raw: continue
                event_time_ist = datetime.fromisoformat(event_time_raw.replace("Z", "+00:00")).astimezone(IST)
                
                if event_time_ist >= now_ist:
                    telugu_name = translate_to_telugu(event_name)
                    date_format = '%I:%M %p' if days == 1 else '%d-%b %I:%M %p'
                    
                    report += f"📅 <b>{event_time_ist.strftime(date_format)}</b>\n🌍 {country}\n🔔 {event_name}\n📝 {telugu_name}\n\n"
                    found_any = True 
                    
        return report if found_any else "ఎటువంటి ఈవెంట్స్ లేవు చంటి గారు."
    except Exception as e: 
        return f"డేటా సేకరించడంలో ఇబ్బంది: {e}"

# --- Live Economic Result Update Check ---
def check_for_live_updates():
    global last_sent_results
    try:
        today = datetime.now(IST).strftime('%Y-%m-%d')
        url = f"https://finnhub.io/api/v1/calendar/economic?from={today}&to={today}&token={FINNHUB_KEY}"
        res = requests.get(url, timeout=20).json()
        for item in res.get("economicCalendar", []):
            event_name = item.get("event", "")
            country = item.get("country", "")
            actual = item.get("actual")
            event_id = f"{event_name}_{country}_{actual}"
            
            if actual is not None and event_id not in last_sent_results:
                if any(c in country for c in ["IN", "US", "JP", "CN", "EU"]):
                    estimate = item.get('estimate', 'N/A')
                    prev = item.get('prev', 'N/A')
                    ai_analysis = get_groq_analysis(f"Event: {event_name}, Actual: {actual}, Expected: {estimate}, Previous: {prev}, Country: {country}")
                    
                    msg = (
                        f"🔔 <b>లైవ్ రిజల్ట్ అప్‌డేట్!</b>\n\n"
                        f"🌍 దేశం: {country}\n"
                        f"🔔 ఈవెంట్: {event_name}\n"
                        f"✅ <b>Actual: {actual}</b>\n"
                        f"📉 Expected: {estimate}\n"
                        f"🔄 Previous: {prev}\n\n"
                        f"🤖 <b>AI విశ్లేషణ:</b> {ai_analysis}"
                    )
                    safe_send(msg)
                    last_sent_results.append(event_id)
    except:
        pass



# --- Workers & Loops ---
def ai_worker():
    while True:
        text, chat_id = ai_queue.get()
        try:
            res_text = safe_gemini(f"Explain this news in 6 lines Telugu: {text}")
            safe_send(f"🧠 <b>AI విశ్లేషణ:</b>\n{res_text}", chat_id=chat_id)
        except:
            pass
        ai_queue.task_done()
        time.sleep(10)

def main_loop():
    global last_reset_date
    while True:
        now_ist_str = datetime.now(IST).strftime("%H:%M")
        current_date = datetime.now(IST).date()
        
        if current_date > last_reset_date:
            sent_alerts.clear()
            sudden_move_sent.clear()
            gap_alert_sent.clear()
            collected_news.clear()
            last_sent_results.clear()
            last_reset_date = current_date
            log("కొత్త రోజు: డేటా రీసెట్ చేయబడింది.")

        # A. Market Open Alerts
        for m_name, (o_time, _) in TIMINGS.items():
            alert_id = f"{m_name}_{current_date}"
            if now_ist_str == o_time and alert_id not in sent_alerts:
                safe_send(f"🔔 <b>MARKET OPEN ALERT</b>\n\n🚀 {m_name} ప్రారంభమైంది! (IST: {o_time})")
                sent_alerts[alert_id] = True 

       # B. Global Table & Moves
        table_content = f"{'-' * 52}\n"
        table_content += f"{'Mkt':<14} {'Price':>9} {'+/-Pts':>8} {'%':>6} {'Trnd':>4}\n"
        table_content += f"{'-' * 52}\n"
        
        for name, sym in symbols.items():
            price, prev_close = get_data(sym)
            if price and prev_close:
                diff = price - prev_close
                change = (diff / prev_close) * 100
                
                check_gap_alert(name, price, prev_close, current_date) 
                
                if abs(change) >= 1.50 and f"{name}_{current_date}_mv" not in sudden_move_sent:
                    safe_send(f"🚨 <b>VOLATILITY ALERT!</b>\n{name}: {change:.2f}% భారీ మార్పు!")
                    sudden_move_sent[f"{name}_{current_date}_mv"] = True 

                # కేవలం ఎమోజీలు మాత్రమే - అక్షరాలు తీసేశాను (image_fcc254.png ఇష్యూ ఫిక్స్)
                if change > 0.3:
                    trend = "📈"
                elif change < -0.3:
                    trend = "📉"
                else:
                    trend = "➖"

                status = is_market_open(name)
                # పేరు లెంగ్త్ ని కంట్రోల్ చేయడం వల్ల టేబుల్ లైన్ తప్పదు
                short_name = name.split(' (')[0][:11]
                
                # పక్కాగా ఒకే వరుసలో వచ్చేలా స్పేసింగ్ సెట్ చేశాను
                table_content += f"{status}{short_name:<12} {price:>9.1f} {diff:>8.1f} {change:>5.1f}% {trend:>2}\n"
        
                # ప్రతి 10 నిమిషాలకు మాత్రమే టేబుల్ పంపాలి
        if datetime.now(IST).minute % 10 == 0:
            try:
                safe_send(f"📊 <b>Global Market Live</b>\n<pre>{table_content}</pre>")
            except:
                pass

        # C. Global News
        for f_url in news_feeds:
            feed = feedparser.parse(f_url)
            for e in feed.entries[:3]:
                if e.title not in sent_news:
                    sent_news.add(e.title)
                    collected_news.append(e.title) 
                    if len(collected_news) > 30: 
                        collected_news.pop(0) 
                    translated = translate_to_telugu(e.title)
                    news_msg = f"🌍 <b>{translated}</b>\n\n🌐 {e.title}\n🔗 <a href='{e.link}'>పూర్తి వార్త ఇక్కడ చూడండి</a>" 
                    safe_send(news_msg, disable_preview=True)
                    if any(k in e.title.lower() for k in ["fed", "war", "oil", "inflation", "cpi", "rate cut"]):
                        ai_queue.put((e.title, CHAT_ID)) 

        gc.collect() 
        time.sleep(60) 

# --- Scheduler Jobs  ---
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

scheduler.add_job(
    lambda: safe_send(f"☀️ <b>నేటి ముఖ్యమైన ఆర్థిక వార్తలు:</b>\n\n{fetch_economic_calendar(1)}"),
    'cron', hour=8, minute=0
)

scheduler.add_job(
    lambda: safe_send(f"📅 <b>వారపు ఆర్థిక క్యాలెండర్:</b>\n\n{fetch_economic_calendar(7)}"),
    'cron', day_of_week='sun', hour=9, minute=0
)

scheduler.add_job(check_for_live_updates, 'interval', minutes=5)


scheduler.start()

# --- Telegram Command Handlers ---
@bot.message_handler(commands=['start', 'today', 'events', 'summary', 'checkindia'])
def handle_commands(m):

    # /start
    if '/start' in m.text:
        safe_send(
            "🚀 బాట్ రెడీ చంటి గారు! గ్లోబల్ (IN, US, JP, CN, EU) డేటా ఫిల్టర్ ఆన్ చేయబడింది.",
            chat_id=m.chat.id
        )
        return

    # /today
    elif '/today' in m.text:
        safe_send(
            fetch_economic_calendar(1),
            chat_id=m.chat.id
        )
        return

    # /events
    elif '/events' in m.text:
        safe_send(
            fetch_economic_calendar(7),
            chat_id=m.chat.id
        )
        return

    # /summary
    elif '/summary' in m.text:
        # ముందుగా "విశ్లేషిస్తున్నాను..." మెసేజ్ పంపించడం
        safe_send(
            "⏳ విశ్లేషిస్తున్నాను...",
            chat_id=m.chat.id
        )

        # వార్తలు లేకపోతే
        if not collected_news:
            safe_send(
                "వార్తలు లేవు.",
                chat_id=m.chat.id
            )
            return

        # AI ద్వారా సమగ్ర మార్కెట్ సమరీ
        res_text = safe_gemini(f"""
మీరు ఒక అనుభవజ్ఞుడైన గ్లోబల్ మార్కెట్ విశ్లేషకుడు.

క్రింది సమాచారాన్ని సమగ్రంగా విశ్లేషించి, చంటి గారికి అర్థమయ్యేలా స్పష్టమైన మరియు సులభమైన తెలుగులో ఒక పూర్తి మార్కెట్ సమరీ ఇవ్వండి.

విశ్లేషణలో తప్పనిసరిగా ఈ అంశాలు ఉండాలి:

1. 🌍 ఈ రోజు వచ్చిన ముఖ్యమైన గ్లోబల్ వార్తలు.
2. 📈 ఏ మార్కెట్లు Gap Up తో ప్రారంభమయ్యాయి, ఎందుకు పెరిగాయి.
3. 📉 ఏ మార్కెట్లు Gap Down తో ప్రారంభమయ్యాయి, ఎందుకు తగ్గాయి.
4. 🚨 Volatility Alerts (1.5% కంటే ఎక్కువ మార్పులు) మరియు వాటి కారణాలు.
5. 🛢️ చమురు, 🥇 బంగారం, ₿ బిట్‌కాయిన్, 🇺🇸 US 10Y Yield ప్రభావం.
6. 🏦 Fed, ECB, RBI లేదా ఇతర కేంద్ర బ్యాంక్ వ్యాఖ్యల ప్రభావం.
7. 📊 CPI, PPI, Inflation, GDP వంటి ఆర్థిక డేటా ప్రభావం.
8. 🇮🇳 రేపటి భారత మార్కెట్ (Nifty/Bank Nifty) పై సాధ్యమైన ప్రభావం.
9. 🎯 మార్కెట్ మొత్తం Bullish, Bearish లేదా Neutral అని స్పష్టంగా చెప్పాలి.

వాడాల్సిన డేటా:

- తాజా గ్లోబల్ వార్తలు:
{' '.join(collected_news[-10:])}

- Gap Alerts:
{', '.join(gap_alert_sent.keys()) if gap_alert_sent else 'ఈ రోజు ముఖ్యమైన Gap Alerts లేవు.'}

- Volatility Alerts:
{', '.join(sudden_move_sent.keys()) if sudden_move_sent else 'ఈ రోజు ముఖ్యమైన Volatility Alerts లేవు.'}

దయచేసి కేవలం తెలుగులో, వివరంగా కానీ సులభంగా అర్థమయ్యే విధంగా సమాధానం ఇవ్వండి.
""")

        # ఫైనల్ రిపోర్ట్ పంపించడం
        safe_send(
            f"📊 <b>మార్కెట్ రిపోర్ట్:</b>\n\n{res_text}",
            chat_id=m.chat.id
        )
        return

    # /checkindia (తరువాత మీరు లాజిక్ జోడించవచ్చు)
    elif '/checkindia' in m.text:
        safe_send(
            "🇮🇳 భారత మార్కెట్ విశ్లేషణ ఫీచర్ త్వరలో అందుబాటులోకి వస్తుంది.",
            chat_id=m.chat.id
        )
        return

# --- Web Server Setup (Render కోసం) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    # Render ఇచ్చే PORT ని వాడుకుంటుంది
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)


# --- Main Execution ---
if __name__ == "__main__":
    log("Bot Starting...")

    try:
        # బాట్ స్టార్ట్ అయినప్పుడు Telegram కి మెసేజ్ వెళ్తుంది
        safe_send("✅ బాట్ విజయవంతంగా ప్రారంభమైంది!")
    except Exception as e:
        log(f"Initial message failed: {e}")

    # 1. Web Server Start (Render కోసం)
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

    # 2. AI Worker Start
    threading.Thread(target=ai_worker, daemon=True).start()

    # 3. Main Loop Start
    threading.Thread(target=main_loop, daemon=True).start()

    # 4. Telegram Bot Polling Start (Auto Restart with Retry)
    while True:
        try:
            log("Starting Bot Polling...")
            bot.infinity_polling(
                timeout=90,
                long_polling_timeout=5,
                skip_pending=True
            )
        except Exception as e:
            log(f"Polling Error: {e}")
            time.sleep(10)   # 10 సెకన్లు ఆగి మళ్లీ ప్రయత్నిస్తుంది
    )
