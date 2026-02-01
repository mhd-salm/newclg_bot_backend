import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import google.generativeai as genai
from dotenv import load_dotenv

# ---------------- LOAD .env (LOCAL ONLY) ---------------- #

load_dotenv()

# ---------------- BASIC CONFIG ---------------- #

app = Flask(__name__)
CORS(app)

MODEL_NAME = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 180

# ---------------- GEMINI KEY FAILOVER (STICKY) ---------------- #

GEMINI_KEYS = os.getenv("GEMINI_KEYS", "").split(",")
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS if k.strip()]

app.logger.info("🔍 Gemini keys loaded:", len(GEMINI_KEYS))  # TEMP

if not GEMINI_KEYS:
    raise RuntimeError("No Gemini API keys found in GEMINI_KEYS")

current_key_index = 0  # 🔒 stick to this key until failure

def get_gemini_model():
    global current_key_index

    attempts = 0
    last_error = None

    while attempts < len(GEMINI_KEYS):
        api_key = GEMINI_KEYS[current_key_index]
        key_number = current_key_index + 1

        app.logger(f"🔑 Using Gemini Key-{key_number}")  # TEMP

        try:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(
                model_name=MODEL_NAME,
                generation_config={
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "temperature": 0.2
                }
            )

        except Exception as e:
            app.logger(f"❌ Gemini Key-{key_number} failed:", str(e))  # TEMP

            last_error = e
            current_key_index += 1
            attempts += 1

            # 🔄 If all keys tried, reset back to key 1
            if current_key_index >= len(GEMINI_KEYS):
                app.logger("🔁 All keys exhausted. Resetting to Key-1")  # TEMP
                current_key_index = 0
                break

    raise RuntimeError(f"All Gemini API keys failed: {last_error}")

# ---------------- SYSTEM IDENTITY ---------------- #

SYSTEM_PROMPT = """
You are the official chatbot of The New College, Chennai.
When the user says "our college" or "newcollege", they mean The New College, Chennai.
Answer briefly, clearly, and factually.
"""

# ---------------- LOAD & CHUNK FILES ---------------- #

DATA_FILES = {
    "college": "college.txt",
    "shift1": "shift1.txt",
    "shift2": "shift2.txt",
    "timetable": "rr.txt",
    "developers":"dev.txt"
}

def load_chunks():
    chunks = []
    for tag, file in DATA_FILES.items():
        if not os.path.exists(file):
            continue
        with open(file, "r", encoding="utf-8") as f:
            text = f.read()
            parts = re.split(r"\n\s*\n", text)
            for p in parts:
                p = p.strip()
                if len(p) > 40:
                    chunks.append({
                        "tag": tag,
                        "text": p.lower(),
                        "raw": p
                    })
    return chunks

CHUNKS = load_chunks()

# ---------------- CHUNK SELECTOR ---------------- #

def select_relevant_chunks(user_msg, limit=3):
    keywords = user_msg.lower().split()
    scored = []

    for c in CHUNKS:
        score = sum(1 for k in keywords if k in c["text"])
        if c["tag"] == "developers" and any(
            k in user_msg.lower()
            for k in ["who made", "who created", "developer", "built you","salman","mustansir","shahid","sathya"]
        ):
            score += 100
        if score > 0:
            scored.append((score, c["raw"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [s[1] for s in scored[:limit]]

# ---------------- CHAT ROUTE ---------------- #

@app.route("/chat", methods=["POST"])
def chat():
    global current_key_index

    data = request.json
    user_msg = data.get("message", "").strip()

    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    relevant_chunks = select_relevant_chunks(user_msg)

    prompt = [SYSTEM_PROMPT]
    if relevant_chunks:
        prompt.append("Relevant college information:")
        for c in relevant_chunks:
            prompt.append(c)
    prompt.append(f"User question: {user_msg}")

    tried_keys = 0
    last_error = None

    while tried_keys < len(GEMINI_KEYS):
        try:
            app.logger(f"🚀 Trying Gemini Key-{current_key_index + 1}")
            model = get_gemini_model()
            response = model.generate_content(prompt)
            return jsonify({"reply": response.text.strip()})
        except Exception as e:
            app.logger("❌ Gemini error:", repr(e))
            msg = str(e).lower()
            last_error = e

            # Retry on quota or other recoverable errors
            if "quota" in msg or "limit" in msg or "429" in msg:
                app.logger("⚠️ Quota hit. Switching key...")
                current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
                tried_keys += 1
                continue

            # Retry on network / API errors
            if "invalid key" in msg or "network" in msg:
                app.logger("⚠️ Recoverable error. Switching key...")
                current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
                tried_keys += 1
                continue

            # Non-recoverable → 500
            return jsonify({"error": str(e)}), 500


    # 🔴 All keys exhausted in this request
    return jsonify({
        "error": "All Gemini API keys are rate-limited. Please try again later."
    }), 429


# ---------------- RUN ---------------- #

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)

