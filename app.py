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

# --------------- CONFIG -----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found. Check your .env file.")

genai.configure(api_key=GEMINI_API_KEY)

APP_PORT = int(os.environ.get("PORT", 4000))
MAX_HISTORY = 20   # keep last N messages when sending to LLM
PDF_LIST = ["college.pdf", "shift1.pdf", "shift2.pdf", "rr.pdf"]
ALLOW_WEB_SEARCH = False  # change to True if you implement search_web()

# --------------- FLASK APP -----------------
app = Flask(__name__)
CORS(app)

# --------------- STORAGE -----------------
college_data = ""      # concatenated text from PDFs
sessions = {}          # in-memory session map {session_id: [ {role, content}, ... ]}

# --------------- LOGGING -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CampusGuide")

# --------------- PDF LOADING -----------------
def load_pdfs(pdf_files):
    """
    Loads text from a list of pdf filenames into the global `college_data` string.
    Skips files that can't be read and continues.
    """
    global college_data
    college_data = ""
    for file in pdf_files:
        if not os.path.isfile(file):
            logger.warning("PDF not found: %s", file)
            continue
        try:
            with open(file, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for i, page in enumerate(reader.pages):
                    try:
                        ptext = page.extract_text()
                        if ptext:
                            college_data += ptext.strip() + "\n\n"
                    except Exception as e_page:
                        logger.debug("Could not read page %d of %s: %s", i, file, e_page)
            logger.info("%s loaded successfully", file)
        except Exception as e:
            logger.warning("Error loading %s: %s", file, e)
    # small normalization
    college_data = re.sub(r"\n{3,}", "\n\n", college_data).strip()

# load at startup
load_pdfs(PDF_LIST)

# --------------- HELPERS -----------------
def compact_history(session_msgs, limit=MAX_HISTORY):
    """Return last `limit` entries as JSON string for prompt (keeps order)."""
    return json.dumps(session_msgs[-limit:], ensure_ascii=False)

def get_day_order_for_date(dt: datetime):
    """
    Return day order number (1..6) for a given date.
    Returns None for Sunday.
    Monday -> 1 ... Saturday -> 6
    """
    weekday = dt.weekday()  # Monday=0 ... Sunday=6
    if weekday == 6:
        return None
    return weekday + 1

def parse_requested_target_date(lower_msg: str, now: datetime):
    """
    Determine target date from message keywords (today, tomorrow, day after).
    Returns a datetime object.
    """
    if "day after" in lower_msg or "day after tomorrow" in lower_msg:
        return now + timedelta(days=2)
    if "tomorrow" in lower_msg:
        return now + timedelta(days=1)
    # Default: today
    return now

def should_use_web(lower_msg: str):
    """Simple heuristic for web search trigger - can be tuned."""
    if not ALLOW_WEB_SEARCH:
        return False
    triggers = ["score", "weather", "news", "who is", "live", "update"]
    return any(t in lower_msg for t in triggers)

def search_web(query: str):
    """Placeholder: return string. Implement if you have a search backend."""
    return "Web search disabled in this deployment."

def build_prompt(extra_instruction: str, doc_text: str, history: str, user_message: str):
    """
    Build a compact, predictable prompt for the model.
    - doc_text: college_data (may be large)
    - history: compact_history(...) JSON
    - extra_instruction: specific directive (could be empty)
    """
    # Keep the prompt compact: include only essential rules and last N messages.
    prompt = f"""
You are CampusGuide AI, a helpful and factual college information assistant.

{extra_instruction}

College PDF Data (ONLY use this for college-specific questions):
-----------------
{doc_text}

Conversation History (recent):
{history}

User Message:
{user_message}

Rules:
- If the user asks about the college (timetable, classes, rooms, policies) use ONLY the PDF data.
- For general questions, answer normally.
- Keep answers short, actionable and friendly.
- If you already know the direct answer (date/time/day-order), answer directly and do not call external search.
"""
    return prompt.strip()

# --------------- ROUTES -----------------
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)
        message = data.get("message", "") or ""
        session_id = data.get("sessionId", "default")

        # init session if needed
        if session_id not in sessions:
            sessions[session_id] = []

        # record user msg
        sessions[session_id].append({"role": "user", "content": message})

        lower_msg = message.lower().strip()
        now = datetime.now()

        # default safety inits
        extra_instruction = ""
        target = now  # default target date

        # short explicit date/time queries
        explicit_date_phrases = {
            "what is the date", "what's the date", "what is today",
            "today date", "today's date", "date today", "give me the date",
            "tell me the date", "date", "today"
        }
        explicit_time_phrases = {
            "what is the time", "what's the time", "current time",
            "time now", "tell me the time", "time"
        }

        is_explicit_date = lower_msg in explicit_date_phrases
        is_explicit_time = lower_msg in explicit_time_phrases
        short_direct = lower_msg in ("date", "time", "today")

        # TIMETABLE / DAY ORDER TRIGGERS
        timetable_keywords = ["timetable", "time table", "schedule", "class", "period", "day order", "dayorder", "day-order"]
        mentions_timetable = any(k in lower_msg for k in timetable_keywords)

        # If user explicitly asks for date/time and not timetable => answer directly
        if (is_explicit_date or is_explicit_time or short_direct) and not mentions_timetable:
            reply = f"Today's date is {now.strftime('%B %d, %Y')}, and the current time is {now.strftime('%I:%M %p')}."
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # If they asked about timetable/day order, compute target date first
        if mentions_timetable or "day order" in lower_msg or "dayorder" in lower_msg:
            target = parse_requested_target_date(lower_msg, now)
            # day order logic
            day_order = get_day_order_for_date(target)
            if day_order is None:
                reply = "It is Sunday. There is no timetable or day order on Sunday."
                sessions[session_id].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

            # If they asked for day order specifically and not timetable content, return simple text
            dayorder_triggers = ["day order", "what's the day order", "what is the day order", "today's day order", "tomorrow's day order", "day order for"]
            if any(t in lower_msg for t in dayorder_triggers) and "timetable" not in lower_msg and "schedule" not in lower_msg:
                # craft a specific reply
                pretty_date = target.strftime("%A, %B %d, %Y")
                # normalize phrasing depending on keywords
                if "tomorrow" in lower_msg:
                    reply = f"Tomorrow ({pretty_date}) is Day Order {day_order}."
                elif "day after" in lower_msg:
                    reply = f"The day after tomorrow ({pretty_date}) is Day Order {day_order}."
                elif "day order" in lower_msg and re.search(r"day\s*order\s*\d", lower_msg):
                    # user asked for or included a number, echo detected or validate
                    reply = f"The day order for {pretty_date} is Day Order {day_order}."
                else:
                    reply = f"Today's Day Order is Day Order {day_order}."
                sessions[session_id].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

            # If they ask for timetable specifically -> set an instruction for the LLM
            if mentions_timetable or "timetable" in lower_msg or "schedule" in lower_msg:
                extra_instruction = f"The user is asking for the timetable for Day Order {day_order}. Extract ONLY the III B.Sc AI timetable from the provided PDF text. Provide the timetable in a short, readable format."

        # If not returned yet, we'll build prompt and call Gemini
        # Optionally run web search (disabled by default)
        web_info = search_web(message) if should_use_web(lower_msg) else ""

        # Build compact history (last N messages)
        history_json = compact_history(sessions[session_id], limit=MAX_HISTORY)

        # Build a trimmed doc_text to avoid excessively large prompt:
        # If college_data is huge, you might want to only include a relevant slice or index. For now we include it.
        doc_text = college_data if college_data else "No college PDFs loaded."

        prompt = build_prompt(extra_instruction=extra_instruction, doc_text=doc_text, history=history_json, user_message=message)
        logger.debug("Prompt length: %d", len(prompt))

        # Call the Gemini model
        model = genai.GenerativeModel("models/gemini-2.0-flash")
        response = model.generate_content(prompt)
        # The SDK may return different shapes; here we try to extract text robustly
        reply = None
        try:
            # Known pattern used earlier
            reply = getattr(response, "text", None) or getattr(response, "content", None) or str(response)
        except Exception:
            reply = str(response)

        # fallback if still empty
        if not reply:
            reply = "Sorry — I couldn't generate a response. Try rephrasing your question."

        # record assistant reply and return
        sessions[session_id].append({"role": "assistant", "content": reply})
        # trim session history to keep memory bounded
        if len(sessions[session_id]) > MAX_HISTORY * 2:
            sessions[session_id] = sessions[session_id][-MAX_HISTORY * 2 :]

        return jsonify({"reply": reply})

    except Exception as e:
        logger.exception("Backend Error")
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500

# --------------- ADMIN ROUTES (optional) -----------------
@app.route("/reload_pdfs", methods=["POST"])
def reload_pdfs():
    """
    Admin endpoint to reload PDFs at runtime without restarting the server.
    POST body: {"pdf_list": ["a.pdf","b.pdf"]}  (optional)
    """
    try:
        data = request.get_json(force=True) or {}
        new_list = data.get("pdf_list", PDF_LIST)
        load_pdfs(new_list)
        return jsonify({"ok": True, "loaded_files_count": len(new_list)})
    except Exception as e:
        logger.exception("Error reloading PDFs")
        return jsonify({"ok": False, "error": str(e)}), 500

# --------------- RUN -----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
   # app.run(port=4000)


