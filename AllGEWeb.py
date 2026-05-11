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
from googlesearch import search
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
TTOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FINNHUB_KEY = os.getenv("FINNHUB_KEY")
GROQ_KEY = os.getenv("GROQ_KEY")

if not all([TOKEN, CHAT_ID, GEMINI_API_KEY, FINNHUB_KEY, GROQ_KEY]):
    print("❌ Error: కొన్ని API Keys సెట్ చేయబడలేదు! Render Env Variables చెక్ చేయండి.")

bot = telebot.TeleBot(TOKEN)

def safe_send(msg, chat_id=CHAT_ID, parse_mode="HTML", disable_preview=False):
    for i in range(3):
        try:
            bot.send_message(
                chat_id,
                msg,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_preview
            )
            return
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
summary_data = []
last_sent_results = []
last_reset_date = datetime.now(IST).date()

# --- మార్కెట్ డేటా & టైమింగ్స్ ---
news_feeds = [
    "https://www.forexlive.com/rss",
    "https://www.investing.com/rss/news_1.rss",
    "https://www.investing.com/rss/news_301.rss",
]

TIMINGS = {
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
    "US 10Y Yield": ("18:30", "03:30"),
    "Bitcoin (Daily)": ("05:30", "05:29"),
}

symbols = {
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
    "Bitcoin (Daily)": "BTC-USD",
    "US 10Y Yield": "^TNX",
}

IMPORTANT_EVENTS = [
    "GDP Growth Rate", "India Fiscal Year GDP Growth", "Inflation Rate YoY", 
    "WPI Inflation YoY", "Industrial Production YoY", "RBI Interest Rate Decision", 
    "Fed Interest Rate Decision", "BoJ Interest Rate Decision", "ECB Interest Rate Decision", 
    "FOMC Minutes", "Non Farm Payrolls", "Initial Jobless Claims", "Retail Sales YoY", 
    "NBS Manufacturing PMI", "Union Budget", "Election Results", "Housing Starts", 
    "New Home Sales", "Existing Home Sales", "Market Holiday"
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
                messages=[{"role": "user", "content": f"మీరు ఒక సీనియర్ స్టాక్ మార్కెట్ ట్రేడర్. ఈ డేటా (Actual vs Expected) ని బట్టి మార్కెట్ సెంటిమెంట్ (Bullish/Bearish) ఎలా ఉండబోతుందో చంటి గారికి 2-3 సూటిగా ఉండే తెలుగు వాక్యాల్లో చెప్పండి. అనవసరమైన సొల్లు లేకుండా ట్రేడింగ్ కి పనికొచ్చే ముక్కలు మాత్రమే చెప్పండి: {prompt_text}"}],
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
        # ఇక్కడ మార్పు చేశాను: మెసేజ్‌ను వేరియబుల్‌లో సేవ్ చేశాను
        msg = f"🚨 <b>GAP ALERT!</b>\n\n{name}\n{direction}: {gap_percent:+.2f}%\nCurrent: {price:.2f} | Prev Close: {prev_close:.2f}"
        
        # టెలిగ్రామ్ కి పంపుతున్నాం
        safe_send(msg)
        
        # AI విశ్లేషణ కోసం డేటాను సేవ్ చేస్తున్నాం
        summary_data.append(msg)
        gap_alert_sent[gap_key] = True 

# --- Economic Calendar Functions ---
def fetch_economic_calendar(days=1):
    try:
        now_ist = datetime.now(IST)
        start_date = now_ist.strftime('%Y-%m-%d')
        end_date = (now_ist + timedelta(days=days)).strftime('%Y-%m-%d')
        url = f"https://finnhub.io/api/v1/calendar/economic?from={start_date}&to={end_date}&token={FINNHUB_KEY}"
        events = requests.get(url, timeout=20).json().get("economicCalendar", [])
        
        report = ""
        found_any = False
        targets = ["IN", "US", "JP", "CN", "EU"] 

        for item in events:
            event_name = item.get("event", "")
            country = item.get("country", "")
            
            # దేశాల ఫిల్టర్
            if any(t in country for t in targets):
                # వారం రిపోర్ట్ (days > 1) అయితేనే IMPORTANT_EVENTS ఫిల్టర్ పనిచేస్తుంది
                if days > 1:
                    if not any(imp in event_name for imp in IMPORTANT_EVENTS):
                        continue
                
                # రోజువారీ (Daily) రిపోర్ట్ కోసం ఫిల్టర్ లేకుండా అన్నీ చూపిస్తుంది
                event_time_raw = item.get("time", "")
                if not event_time_raw: continue
                event_time_ist = datetime.fromisoformat(event_time_raw.replace("Z", "+00:00")).astimezone(IST)
                
                # ఈరోజు జరిగినవి మరియు జరగబోయేవి అన్నీ చూపిస్తుంది (కేవలం జరిగిపోయిన రోజులు తప్ప)
                if event_time_ist.date() >= now_ist.date():
                    telugu_name = translate_to_telugu(event_name)
                    date_format = '%I:%M %p' if days == 1 else '%d-%b %I:%M %p'
                    
                    report += f"📅 <b>{event_time_ist.strftime(date_format)}</b>\n🌍 {country}\n🔔 {event_name}\n📝 {telugu_name}\n\n"
                    found_any = True 
                    
        return report if found_any else "ఈరోజుకి ఎటువంటి ఈవెంట్స్ షెడ్యూల్ చేయబడలేదు చంటి గారు."
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
                    summary_data.append(msg)
                    last_sent_results.append(event_id)
    except:
        pass

def search_indian_market_data(query_type, use_ai=False):
    search_query = f"latest India {query_type} news {datetime.now(IST).strftime('%B %Y')}"
    try:
        results = [res for res in search(search_query, num_results=3)] 
        if results:
            links_text = "\n".join([f"🔗 {r}" for r in results])
            analysis = get_groq_analysis(f"Summary for {query_type}: {links_text}") if use_ai else ""

            msg = (
                f"📊 <b>ముఖ్యమైన అప్‌డేట్: {query_type}</b>\n\n"
                f"🤖 <b>AI విశ్లేషణ:</b> {analysis}\n\n"
                f"{links_text}"
            )
            safe_send(msg, disable_preview=True)
    except:
        pass

# --- Market Table Function (ధరల టేబుల్ కోసం కొత్త ఫంక్షన్) ---
def send_global_table():
    current_date = datetime.now(IST).date()
    table_content = "-" * 55 + "\n"
    for name, sym in symbols.items():
        price, prev_close = get_data(sym)
        if price and prev_close:
            change = ((price - prev_close) / prev_close) * 100
            check_gap_alert(name, price, prev_close, current_date) 
            
            # భారీ మార్పులు ఉంటే అలర్ట్ ఇస్తుంది
            # --- ఇక్కడ మీరు అడిగిన మార్పు చేయాలి ---
            if abs(change) >= 1.50 and f"{name}_{current_date}_mv" not in sudden_move_sent:
                # 1. మెసేజ్‌ను వేరియబుల్‌లో పెడుతున్నాం
                v_msg = f"🚨 <b>VOLATILITY ALERT!</b>\n{name}: {change:.2f}% భారీ మార్పు!"
                
                # 2. పంపుతున్నాం
                safe_send(v_msg)
                
                # 3. సమ్మరీ డేటాలోకి యాడ్ చేస్తున్నాం
                summary_data.append(v_msg) 
                
                sudden_move_sent[f"{name}_{current_date}_mv"] = True
                
            trend = "📈UP" if change > 0.5 else "📉DN" if change < -0.5 else "➖FT"
            table_content += f"{is_market_open(name)} {name:<20} {price:>10.2f} ({change:>+7.2f}%) {trend}\n" 
    
    try: 
        safe_send(f"📊 <b>Global Market Live</b>\n<pre>{table_content}</pre>") 
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
            summary_data.clear()
            last_reset_date = current_date
            log("కొత్త రోజు: డేటా రీసెట్ చేయబడింది.")

        # A. Market Open Alerts (ప్రతి నిమిషం చెక్ చేస్తుంది - అలర్ట్ మిస్ అవ్వదు)
        for m_name, (o_time, _) in TIMINGS.items():
            alert_id = f"{m_name}_{current_date}"
            if now_ist_str >= o_time and alert_id not in sent_alerts:
                safe_send(f"🔔 <b>MARKET OPEN ALERT</b>\n\n🚀 {m_name} ప్రారంభమైంది! (IST: {o_time})")
                sent_alerts[alert_id] = True 

        # B. Global News
        for f_url in news_feeds:
            feed = feedparser.parse(f_url)
            for e in feed.entries[:3]:
                if e.title not in sent_news:
                    sent_news.add(e.title)
                    collected_news.append(e.title) 
                    translated = translate_to_telugu(e.title)
                    news_msg = f"🌍 <b>{translated}</b>\n\n🌐 {e.title}\n🔗 <a href='{e.link}'>పూర్తి వార్త ఇక్కడ చూడండి</a>" 
                    safe_send(news_msg, disable_preview=True)
                    if any(k in e.title.lower() for k in ["fed", "war", "oil", "inflation", "cpi", "rate cut"]):
                        ai_queue.put((e.title, CHAT_ID)) 

        gc.collect() 
        time.sleep(60) # నిద్ర సమయం 1 నిమిషానికి తగ్గించాం

# --- Scheduler Jobs ---
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

# ఇక్కడ ప్రతి 5 నిమిషాలకు ధరల టేబుల్ వచ్చేలా సెట్ చేశాం
scheduler.add_job(send_global_table, 'interval', minutes=10)

scheduler.add_job(
    lambda: safe_send(f"☀️ <b>నేటి ముఖ్యమైన ఆర్థిక వార్తలు:</b>\n\n{fetch_economic_calendar(1)}"),
    'cron', hour=8, minute=0
)

scheduler.add_job(
    lambda: safe_send(f"📅 <b>వారపు ఆర్థిక క్యాలెండర్:</b>\n\n{fetch_economic_calendar(7)}"),
    'cron', day_of_week='sun', hour=9, minute=0
)

scheduler.add_job(check_for_live_updates, 'interval', minutes=10)
scheduler.add_job(lambda: search_indian_market_data("Auto Sales", True), 'cron', day='1-5', hour='10,13,16')
scheduler.add_job(lambda: search_indian_market_data("AMFI MF Inflows", True), 'cron', day='7-10', hour='11,16')
scheduler.add_job(lambda: search_indian_market_data("TRAI Data", True), 'cron', day='19-24', hour='12,17')

scheduler.start()

# --- Telegram Command Handlers ---
@bot.message_handler(commands=['start', 'today', 'events', 'summary', 'checkindia']) 
def handle_commands(m):
    if '/start' in m.text: 
        safe_send("🚀 బాట్ రెడీ చంటి గారు! గ్లోబల్ (IN, US, JP, CN, EU) డేటా ఫిల్టర్ ఆన్ చేయబడింది.", chat_id=m.chat.id)

    elif '/today' in m.text: 
        safe_send(fetch_economic_calendar(1), chat_id=m.chat.id)

    elif '/events' in m.text: 
        safe_send(fetch_economic_calendar(7), chat_id=m.chat.id)

    elif '/checkindia' in m.text: 
        search_indian_market_data("Stock Market India", False)

    elif '/summary' in m.text:
        safe_send("⏳ మార్కెట్ కదలికల వెనుక కారణాలను విశ్లేషిస్తున్నాను...", chat_id=m.chat.id)
        
        # వార్తలు (News) మరియు అలర్ట్స్ (Alerts) రెండింటినీ కలుపుతున్నాం
        # తాజా 20 వార్తలు, 15 అలర్ట్స్ తీసుకుంటున్నాం
        all_info = "NEWS DATA:\n" + "\n".join(collected_news[-20:]) + "\n\nMARKET ALERTS:\n" + "\n".join(summary_data[-15:])
        
        if not collected_news and not summary_data:
            safe_send("విశ్లేషించడానికి ప్రస్తుతానికి ఎటువంటి డేటా లేదు చంటి గారు.", chat_id=m.chat.id)
            return

        prompt = f"""
        మీరు ఒక సీనియర్ స్టాక్ మార్కెట్ అనలిస్ట్. ఈ కింద ఉన్న డేటాను జాగ్రత్తగా చదవండి:
        {all_info}
        
        పైన ఉన్న సమాచారం ఆధారంగా చంటి గారి కోసం ఈ కింది పద్ధతిలో విశ్లేషణ ఇవ్వండి:
        1. **ఏమి జరిగింది?**: ఈరోజు మార్కెట్లో వచ్చిన ముఖ్యమైన గ్యాప్స్ (Gaps) లేదా భారీ కదలికల గురించి చెప్పండి.
        2. **ఎందుకు జరిగింది?**: (అతి ముఖ్యం) ఈ మార్కెట్ కదలికలకు గల కారణాన్ని 'NEWS DATA' లోని వార్తలతో లింక్ చేసి వివరించండి. (ఉదా: 'చమురు ధరల పెంపు వల్ల' లేదా 'US Inflation డేటా వల్ల' అని స్పష్టంగా చెప్పాలి).
        3. **ట్రేడింగ్ సలహా**: ఈ పరిస్థితిలో ట్రేడర్స్ జాగ్రత్తగా ఉండాలా లేదా ఏదైనా అవకాశం ఉందా?
        
        సూచన: 8-10 లైన్లలో, స్పష్టమైన తెలుగులో, పాయింట్ల వారీగా వివరించండి. డేటాలో లేని విషయాలను ఊహించి చెప్పకండి.
        """
        
        res_text = safe_gemini(prompt)
        safe_send(f"📊 <b>సమగ్ర మార్కెట్ విశ్లేషణ (కారణాలతో సహా):</b>\n\n{res_text}", chat_id=m.chat.id)

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
        # బాట్ స్టార్ట్ అయినప్పుడు టెలిగ్రామ్ కి మెసేజ్ వెళ్తుంది
        safe_send("✅ బాట్ విజయవంతంగా ప్రారంభమైంది!")
    except Exception as e:
        log(f"Initial message failed: {e}")

    # 1. వెబ్ సర్వర్ స్టార్ట్ (Render బాట్ ని ఆపకుండా ఉండటానికి)
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
    
    # 2. AI Worker స్టార్ట్
    threading.Thread(target=ai_worker, daemon=True).start()
    
    # 3. మెయిన్ లూప్ స్టార్ట్
    threading.Thread(target=main_loop, daemon=True).start()
    
    # 4. టెలిగ్రామ్ బాట్ పోలింగ్ స్టార్ట్ (ఇది మెయిన్ థ్రెడ్ లో రన్ అవుతుంది)
    log("Starting Bot Polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=5)
