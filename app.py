# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import PyPDF2
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timedelta
import re
import logging

# ------------------- CONFIG -------------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ ERROR: GEMINI_API_KEY missing from .env")

genai.configure(api_key=GEMINI_API_KEY)

APP_PORT = 8080
MAX_HISTORY = 5
PDF_FOLDER = "pdfs"

PDF_LIST = ["college.pdf", "shift1.pdf", "shift2.pdf", "rr.pdf"]

# ------------------- FLASK -------------------
app = Flask(__name__)
CORS(app)

# ------------------- STORAGE -------------------
sessions = {}
college_data = ""

# ------------------- LOGGING -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CampusGuide")


# ------------------- LOAD PDFs -------------------
def load_pdfs():
    global college_data
    college_data = ""

    for file in PDF_LIST:
        full_path = os.path.join(PDF_FOLDER, file)

        if not os.path.isfile(full_path):
            logger.warning("PDF missing: %s", full_path)
            continue

        try:
            with open(full_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        college_data += text + "\n\n"

            logger.info("Loaded PDF: %s", full_path)

        except Exception as e:
            logger.error("Error loading %s: %s", full_path, e)

    college_data = re.sub(r"\n{3,}", "\n\n", college_data).strip()


load_pdfs()


# ---------------- TIMETABLE EXTRACTOR ----------------
def extract_timetable(day_order: int):
    if not college_data:
        return None

    pattern = rf"DAY\s*ORDER\s*{day_order}(.*?)(DAY\s*ORDER\s*[1-6]|$)"
    match = re.search(pattern, college_data, re.IGNORECASE | re.DOTALL)

    if not match:
        return None

    text = match.group(1).strip()
    return re.sub(r"\n{2,}", "\n", text)


# ------------------- HELPERS -------------------
def parse_date(lower_msg: str, now: datetime):
    if "day after" in lower_msg or "day after tomorrow" in lower_msg:
        return now + timedelta(days=2)
    if "tomorrow" in lower_msg:
        return now + timedelta(days=1)
    return now


def day_order_from_date(dt: datetime):
    weekday = dt.weekday()
    if weekday == 6:
        return None
    return weekday + 1


def trim_history(session_msgs):
    return json.dumps(session_msgs[-MAX_HISTORY:], ensure_ascii=False)


def build_prompt(user_msg, pdf_text, history):
    return f"""
You are **Campus Guide AI** — always helpful, short, and accurate.

Rules:
- ONLY use PDF data for timetable/day-order/college academic questions.
- If question is not about college — reply normally.
- Keep answers clear and short.

PDF Data (only included when required):
--------------------------------------
{pdf_text}

Chat History:
{history}

User Message:
{user_msg}
""".strip()


# ------------------- MAIN CHAT ENDPOINT -------------------
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)
        message = data.get("message", "")
        session_id = data.get("sessionId", "default")

        if session_id not in sessions:
            sessions[session_id] = []

        sessions[session_id].append({"role": "user", "content": message})

        lower = message.lower().strip()
        now = datetime.now()

        # DATE ONLY
        if lower in {"what is the date", "what's the date", "today date", "today's date",
                     "date today", "what is today"} and "timetable" not in lower:
            reply = f"📅 Today's date is {now.strftime('%B %d, %Y')}."
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # TIME ONLY
        if lower in {"what is the time", "time now", "current time", "tell me the time"} and "timetable" not in lower:
            reply = f"⏰ Current time: {now.strftime('%I:%M %p')}."
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # TIMETABLE
        timetable_keywords = ["timetable", "time table", "schedule", "class", "period"]
        if any(k in lower for k in timetable_keywords) or "day order" in lower:
            target_date = parse_date(lower, now)
            day_order = day_order_from_date(target_date)

            if day_order is None:
                reply = "📌 It is Sunday — no classes today."
                sessions[session_id].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

            table = extract_timetable(day_order)
            pretty = target_date.strftime("%A, %B %d, %Y")

            if table:
                reply = f"📅 **Timetable for {pretty} (Day Order {day_order})**\n\n{table}"
            else:
                reply = f"Day Order {day_order} found, but timetable section missing in PDF."

            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # NORMAL CHAT
        history_json = trim_history(sessions[session_id])
        needs_pdf = any(k in lower for k in timetable_keywords) or "day order" in lower
        pdf_text = college_data if needs_pdf else "Not required."

        prompt = build_prompt(message, pdf_text, history_json)

        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        response = model.generate_content(prompt)

        reply = getattr(response, "text", None) or str(response)

        sessions[session_id].append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})

    except Exception as e:
        logger.exception("SERVER ERROR")
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500


# ------------------- RELOAD PDFs -------------------
@app.route("/reload_pdfs", methods=["POST"])
def reload_pdf_route():
    try:
        load_pdfs()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------- RUN -------------------
if __name__ == "__main__":
    # Render / Railway / Heroku etc. will provide PORT
    port = int(os.environ.get("PORT", APP_PORT))

    # Bind to 0.0.0.0 so works inside Docker/Render
    app.run(host="0.0.0.0", port=port)
