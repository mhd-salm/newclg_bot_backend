from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timedelta
import re
import logging

# --------------- CONFIG -----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found. Check your .env file.")

genai.configure(api_key=GEMINI_API_KEY)

APP_PORT = int(os.environ.get("PORT", 4000))
MAX_HISTORY = 2   # keep last N messages
TEXT_FILES = ["college.txt", "shift1.txt", "shift2.txt", "rr.txt"]
ALLOW_WEB_SEARCH = False

# --------------- FLASK APP -----------------
app = Flask(__name__)
CORS(app)

# --------------- STORAGE -----------------
college_texts = {}   # {filename: text}
sessions = {}        # in-memory sessions

# --------------- LOGGING -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CampusGuide")

# --------------- TEXT FILE LOADING -----------------
def load_text_files(text_files):
    global college_texts
    college_texts = {}

    for file in text_files:
        if not os.path.isfile(file):
            logger.warning("Text file not found: %s", file)
            continue
        try:
            with open(file, "r", encoding="utf-8") as f:
                college_texts[file] = f.read()
            logger.info("%s loaded successfully", file)
        except Exception as e:
            logger.warning("Error loading %s: %s", file, e)

# Load at startup
load_text_files(TEXT_FILES)

# --------------- HELPERS -----------------
def compact_history(session_msgs, limit=MAX_HISTORY):
    return "\n".join(
        f"{m['role'].capitalize()}: {m['content']}"
        for m in session_msgs[-limit:]
    )

def get_day_order_for_date(dt: datetime):
    weekday = dt.weekday()  # Monday=0 ... Sunday=6
    if weekday == 6:
        return None
    return weekday + 1

def parse_requested_target_date(lower_msg: str, now: datetime):
    if "day after" in lower_msg or "day after tomorrow" in lower_msg:
        return now + timedelta(days=2)
    if "tomorrow" in lower_msg:
        return now + timedelta(days=1)
    return now

def search_text_files(query, max_lines=25):
    query_words = set(query.lower().split())
    results = []

    for fname, text in college_texts.items():
        for line in text.splitlines():
            line_lower = line.lower()
            score = sum(1 for w in query_words if w in line_lower)
            if score > 0:
                results.append((score, f"[{fname}] {line.strip()}"))

    results.sort(reverse=True, key=lambda x: x[0])
    return "\n".join(r for _, r in results[:max_lines])

def build_prompt(extra_instruction, doc_text, history, user_message):
    return f"""
You are CampusGuide AI, a college information assistant.

Use ONLY the provided text context for college-related questions.

{extra_instruction}

Text Context:
{doc_text}

Recent Chat:
{history}

User:
{user_message}

Answer briefly and clearly.
""".strip()

# --------------- ROUTES -----------------
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)
        message = data.get("message", "") or ""
        session_id = data.get("sessionId", "default")

        if session_id not in sessions:
            sessions[session_id] = []

        sessions[session_id].append({"role": "user", "content": message})

        lower_msg = message.lower().strip()
        now = datetime.now()
        extra_instruction = ""
        target = now

        # ---- DATE / TIME QUICK ANSWERS ----
        if lower_msg in ("date", "today", "time"):
            reply = f"Today's date is {now.strftime('%B %d, %Y')} and the time is {now.strftime('%I:%M %p')}."
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # ---- TIMETABLE / DAY ORDER ----
        timetable_keywords = ["timetable", "schedule", "class", "period", "day order", "dayorder"]
        mentions_timetable = any(k in lower_msg for k in timetable_keywords)

        if mentions_timetable:
            target = parse_requested_target_date(lower_msg, now)
            day_order = get_day_order_for_date(target)

            if day_order is None:
                reply = "It is Sunday. There is no timetable or day order on Sunday."
                sessions[session_id].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

            if "day order" in lower_msg and "timetable" not in lower_msg:
                pretty = target.strftime("%A, %B %d, %Y")
                reply = f"{pretty} is Day Order {day_order}."
                sessions[session_id].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

            extra_instruction = (
                f"The user is asking for the timetable for Day Order {day_order}. "
                f"Extract ONLY the III B.Sc AI timetable from the text context."
            )

        # ---- SEARCH TEXT FILES ----
        doc_text = search_text_files(message)
        if not doc_text:
            doc_text = "No relevant college data found."

        history = compact_history(sessions[session_id])
        prompt = build_prompt(extra_instruction, doc_text, history, message)

        model = genai.GenerativeModel("models/gemini-2.5-flash")
        response = model.generate_content(prompt)
        reply = getattr(response, "text", None) or str(response)

        sessions[session_id].append({"role": "assistant", "content": reply})

        if len(sessions[session_id]) > MAX_HISTORY * 2:
            sessions[session_id] = sessions[session_id][-MAX_HISTORY * 2:]

        return jsonify({"reply": reply})

    except Exception as e:
        logger.exception("Backend Error")
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500

# --------------- ADMIN ROUTE -----------------
@app.route("/reload_texts", methods=["POST"])
def reload_texts():
    try:
        data = request.get_json(force=True) or {}
        files = data.get("text_files", TEXT_FILES)
        load_text_files(files)
        return jsonify({"ok": True, "files_loaded": list(college_texts.keys())})
    except Exception as e:
        logger.exception("Reload Error")
        return jsonify({"ok": False, "error": str(e)}), 500

# --------------- RUN -----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    app.run(host="0.0.0.0", port=port)
    #app.run(port=4000)






