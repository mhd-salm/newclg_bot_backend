
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta
import google.generativeai as genai

# ================= CONFIG =================
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=GEMINI_API_KEY)

APP_PORT = int(os.environ.get("PORT", 4000))
MAX_HISTORY = 2
TEXT_FILES = ["college.txt", "rr.txt", "shift1.txt", "shift2.txt"]

# ================= APP ====================
app = Flask(__name__)
CORS(app)

# ================= LOGGING ================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CampusGuide")

# ================= STORAGE ================
raw_texts = {}      # filename -> raw text
text_chunks = []    # [{file, chunk}]
sessions = {}

# ================= TEXT LOADING ============
def normalize_text(text: str) -> str:
    text = text.replace("\t", " ")
    text = re.sub(r" +", " ", text)
    return text.strip()


def split_into_chunks(text: str):
    """
    VERY IMPORTANT:
    - Splits by blank lines (paragraphs)
    - Also keeps table rows together
    """
    blocks = re.split(r"\n\s*\n", text)
    chunks = []

    for block in blocks:
        block = normalize_text(block)
        if len(block) < 40:
            continue
        chunks.append(block)

    return chunks


def load_text_files(files):
    global raw_texts, text_chunks
    raw_texts = {}
    text_chunks = []

    for file in files:
        if not os.path.isfile(file):
            logger.warning("Missing file: %s", file)
            continue

        with open(file, "r", encoding="utf-8") as f:
            text = f.read()

        raw_texts[file] = text
        chunks = split_into_chunks(text)

        for ch in chunks:
            text_chunks.append({"file": file, "chunk": ch})

        logger.info("Loaded %s (%d chunks)", file, len(chunks))


load_text_files(TEXT_FILES)

# ================= HELPERS =================
def compact_history(msgs, limit=MAX_HISTORY):
    return "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in msgs[-limit:]
    )


def score_chunk(chunk: str, query: str) -> int:
    score = 0
    q_words = set(query.lower().split())
    chunk_l = chunk.lower()

    for w in q_words:
        if w in chunk_l:
            score += 2

    if "fee" in q_words and "total" in chunk_l:
        score += 3

    if "b.sc" in chunk_l or "ai" in chunk_l:
        score += 1

    return score


def retrieve_relevant_chunks(query, top_k=6):
    scored = []
    for item in text_chunks:
        s = score_chunk(item["chunk"], query)
        if s > 0:
            scored.append((s, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = scored[:top_k]
    return "\n\n".join(
        f"[SOURCE: {i['file']}]\n{i['chunk']}"
        for _, i in selected
    )


def build_prompt(context, history, user_msg):
    return f"""
You are CampusGuide AI.

STRICT RULES:
- Use ONLY the provided context
- If info is missing, say: "Not available in college records"
- Do NOT guess or calculate unless explicitly present

CONTEXT:
{context}

RECENT CHAT:
{history}

USER QUESTION:
{user_msg}

Answer clearly and briefly.
""".strip()


def get_day_order(dt):
    if dt.weekday() == 6:
        return None
    return dt.weekday() + 1


def parse_date(msg, now):
    if "day after tomorrow" in msg:
        return now + timedelta(days=2)
    if "tomorrow" in msg:
        return now + timedelta(days=1)
    return now

# ================= ROUTES ==================
@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)
        msg = (data.get("message") or "").strip()
        sid = data.get("sessionId", "default")

        sessions.setdefault(sid, [])
        sessions[sid].append({"role": "user", "content": msg})

        lower = msg.lower()
        now = datetime.now()

        # Quick date/time
        if lower in {"date", "time", "today"}:
            reply = f"Today is {now.strftime('%B %d, %Y')} ({now.strftime('%I:%M %p')})."
            sessions[sid].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        # Day order logic
        if "day order" in lower or "timetable" in lower:
            target = parse_date(lower, now)
            order = get_day_order(target)
            if order is None:
                reply = "Sunday has no classes or timetable."
                sessions[sid].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

        context = retrieve_relevant_chunks(msg)
        if not context:
            reply = "Not available in college records."
            sessions[sid].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        history = compact_history(sessions[sid])
        prompt = build_prompt(context, history, msg)

        model = genai.GenerativeModel("models/gemini-2.5-flash")
        res = model.generate_content(prompt)
        reply = getattr(res, "text", "Not available in college records.")

        sessions[sid].append({"role": "assistant", "content": reply})
        sessions[sid] = sessions[sid][-MAX_HISTORY * 2:]

        return jsonify({"reply": reply})

    except Exception as e:
        logger.exception("Chat error")
        return jsonify({"reply": f"Server error: {e}"}), 500


@app.route("/reload_texts", methods=["POST"])
def reload_texts():
    load_text_files(TEXT_FILES)
    return jsonify({"ok": True, "chunks": len(text_chunks)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT)
   # app.run(port=4000)
