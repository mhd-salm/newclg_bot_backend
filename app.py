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
    raise ValueError("GEMINI_API_KEY not found. Check your .env file.")

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

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

pdf_list = ["college.pdf", "shift1.pdf", "shift2.pdf",  "timetable(ai).pdf"]
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

        lower_msg = message.lower().strip()

        # ------------------------------------------------------
        # STRICT DATE / TIME DETECTION
        # ------------------------------------------------------

        explicit_date_phrases = [
            "what is the date",
            "what's the date",
            "what is today",
            "today date",
            "today's date",
            "date today",
            "give me the date",
            "tell me the date"
        ]

        explicit_time_phrases = [
            "what is the time",
            "what's the time",
            "current time",
            "time now",
            "tell me the time"
        ]

        is_explicit_date = lower_msg in explicit_date_phrases
        is_explicit_time = lower_msg in explicit_time_phrases

        short_direct = lower_msg in ("date", "time", "today")

        timetable_keywords = [
            "timetable", "time table", "schedule", "class", "period",
            "day order", "dayorder", "day-order"
        ]

        mentions_timetable = any(k in lower_msg for k in timetable_keywords)

        if (is_explicit_date or is_explicit_time or short_direct) and not mentions_timetable:
            now = datetime.now()
            reply = (
                f"Today's date is {now.strftime('%B %d, %Y')}, "
                f"and the current time is {now.strftime('%I:%M %p')}."
            )
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # ------------------------------------------------------
        # DAY ORDER DETECTION
        # ------------------------------------------------------

        dayorder_triggers = [
            "day order", "dayorder", "day-order",
            "what's the day order", "what is the day order",
            "today's day order", "tomorrow's day order",
            "day order for"
        ]

        if any(t in lower_msg for t in dayorder_triggers):

            def get_target_date_from_text(s):
                now = datetime.now()
                if "day after" in s:
                    return datetime.fromordinal(now.toordinal() + 2)
                if "tomorrow" in s:
                    return datetime.fromordinal(now.toordinal() + 1)

                week_map = {
                    "monday": 0, "tuesday": 1, "wednesday": 2,
                    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
                }

                for name, idx in week_map.items():
                    if name in s:
                        d = datetime.now()
                        while d.weekday() != idx:
                            d = datetime.fromordinal(d.toordinal() + 1)
                        return d

                return now

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
            wd = target.weekday()

            if wd == 6:
                reply = "It is Sunday. There is no day order on Sunday."
            else:
                day_order = wd + 1
                if "tomorrow" in lower_msg:
                    reply = f"Tomorrow's day order is Day Order {day_order}."
                elif "day after" in lower_msg:
                    reply = f"The day after tomorrow is Day Order {day_order}."
                elif "today" in lower_msg:
                    reply = f"Today's day order is Day Order {day_order}."
                else:
                    reply = f"The day order for that day is Day Order {day_order}."

            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # ------------------------------------------------------
        # WEB SEARCH TRIGGER
        # ------------------------------------------------------
        use_web = any(word in lower_msg for word in ["score", "weather", "news", "who is", "live", "update"])
        web_info = search_web(message) if use_web else ""

        # ------------------------------------------------------
        # LLM PROMPT
        # ------------------------------------------------------

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
- If the question is about the college → use PDF data only.
- If it is a general question → answer normally.
- If web search info exists → include it.
- Keep answers simple and helpful.
"""

        model = genai.GenerativeModel("models/gemini-2.0-flash")
        response = model.generate_content(prompt)
        reply = response.text

        sessions[session_id].append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})

    except Exception as e:
        print("Backend Error:", e)
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500


# ---------------------- RUN SERVER ----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

    #app.run(port=4000)

    



