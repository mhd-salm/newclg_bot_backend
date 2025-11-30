from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import PyPDF2
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime
import re

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found. Check your .env file location.")
# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = Flask(__name__)
CORS(app)

# Global storage
college_data = ""
sessions = {}

# ---------------------- PDF LOADING ----------------------

def load_pdfs(pdf_files):
    global college_data
    college_data = ""

    for file in pdf_files:
        try:
            with open(file, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    ptext = page.extract_text()
                    if ptext:
                        college_data += ptext + "\n"
            print(f"{file} loaded successfully!")
        except Exception as e:
            print(f"Error loading {file}:", e)

pdf_list = ["college.pdf", "shift1.pdf","shift2.pdf","DailyTT.pdf","timetable(ai).pdf"]
load_pdfs(pdf_list)

# ---------------------- OPTIONAL WEB SEARCH ----------------------

def search_web(query):
    return "Web search unavailable in this version."

# ---------------------- CHAT ROUTE ----------------------

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        message = data.get("message", "")
        session_id = data.get("sessionId", "default")

        if session_id not in sessions:
            sessions[session_id] = []

        sessions[session_id].append({"role": "user", "content": message})

        # ---- Date/Time Detection (strict) ----
        # Previously this matched any occurrence of words like "day"
        # and caused the server to reply with the current date/time even
        # when the user asked unrelated questions (e.g. "today timetable").
        #
        # New logic: only treat the message as a direct date/time query
        # when it explicitly asks for date/time (short queries like
        # "today", "what is the date", "what time is it", "current time",
        # etc.). We explicitly avoid answering date/time when the message
        # includes timetable/day-order related keywords so those intents
        # can be handled elsewhere.
        lower_msg = message.lower().strip()

        # Detect explicit date/time questions using simple regexes and
        # exact short phrases.
        is_explicit_date = bool(re.search(r"\b(what(?:'s| is)?\s+the\s+date|what(?:'s| is)?\s+today|date\s+today|today's\s+date|\bdate\b)\b", lower_msg))
        is_explicit_time = bool(re.search(r"\b(what(?:'s| is)?\s+the\s+time|what\s+time|current\s+time|time\s+now|what(?:'s| is)?\s+the\s+time\b)\b", lower_msg))
        is_short_direct = lower_msg in ("today", "time", "date", "what's the date", "what is today", "what is the date")

        # Keywords that indicate the user is asking about timetables/day-orders
        # — in those cases we should NOT short-circuit and return the date/time.
        timetable_keywords = ["timetable", "time table", "schedule", "class", "classes", "period", "day order", "dayorder", "day-order"]
        mentions_timetable = any(k in lower_msg for k in timetable_keywords)

        # --- Day-order quick handler (small addition) ---
        dayorder_triggers = ["day order", "dayorder", "day-order", "what's the day order", "what is the day order", "today's day order", "tomorrow's day order", "day order for"]
        mentions_dayorder = any(t in lower_msg for t in dayorder_triggers)

        if mentions_dayorder:
            # helper to get target date
            def get_target_date_from_text(s):
                now = datetime.now()
                if "day after" in s:
                    return datetime.fromordinal(now.toordinal() + 2)
                if "tomorrow" in s:
                    return datetime.fromordinal(now.toordinal() + 1)
                # weekday names
                week_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
                for name, idx in week_map.items():
                    if name in s:
                        d = datetime.now()
                        while d.weekday() != idx:
                            d = datetime.fromordinal(d.toordinal() + 1)
                        return d
                return datetime.now()

            # explicit number
            m = re.search(r"day\s*order\s*(\d)", lower_msg)
            if m:
                try:
                    n = int(m.group(1))
                    if 1 <= n <= 6:
                        reply = f"Day Order {n}."
                        sessions[session_id].append({"role": "assistant", "content": reply})
                        return jsonify({"reply": reply})
                except:
                    pass

            target = get_target_date_from_text(lower_msg)
            # Monday => 1, Tuesday=>2, ..., Saturday=>6, Sunday=>holiday (None)
            wd = target.weekday()  # Monday=0 .. Sunday=6
            if wd == 6:
                reply = "It is Sunday. There is no day order on Sunday."
            else:
                day_order = wd + 1
                if "tomorrow" in lower_msg:
                    reply = f"Tomorrow's day order is Day Order {day_order}."
                elif "day after" in lower_msg:
                    reply = f"The day after tomorrow is Day Order {day_order}."
                elif "today" in lower_msg or lower_msg.strip() in ("what's the day order", "what is the day order", "today's day order"):
                    reply = f"Today's day order is Day Order {day_order}."
                else:
                    reply = f"The day order for that day is Day Order {day_order}."

            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        if (is_explicit_date or is_explicit_time or is_short_direct) and not mentions_timetable:
            now = datetime.now()
            reply = (
                f"Today's date is {now.strftime('%B %d, %Y')}, "
                f"and the current time is {now.strftime('%I:%M %p')}.")
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # ---- Web Search Trigger ----
        use_web = any(word in message.lower() for word in ["score", "weather", "news", "who is", "live", "update"])
        web_info = search_web(message) if use_web else ""

        # ---- LLM Prompt ----
        prompt = f"""
You are CampusGuide AI, a college information assistant.

College PDF Data:
-----------------
{college_data}

Web Search Results:
-------------------
{web_info}

Conversation History:
{json.dumps(sessions[session_id])}

User Message:
{message}

Rules:
- If question is about the college → answer using PDF data only.
- If it's a general question → answer normally.
- If web search info exists → include it.
- Keep responses simple, accurate, and helpful.
"""

        # Gemini Model Call
        model = genai.GenerativeModel("models/gemini-2.0-flash")
        response = model.generate_content(prompt)
        reply = response.text

        sessions[session_id].append({"role": "assistant", "content": reply})

        return jsonify({"reply": reply})

    except Exception as e:
        print("Backend Error:", e)
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500

# ---------------------- RUN SERVER ----------------------
'''
if __name__ == "__main__":
    app.run(port=4000)'''
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    


