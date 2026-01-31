import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import itertools
import google.generativeai as genai
from dotenv import load_dotenv   # 🔹 NEW

# ---------------- LOAD .env (LOCAL ONLY) ---------------- #

load_dotenv()  # 🔹 Loads GEMINI_KEYS from .env when running locally

# ---------------- BASIC CONFIG ---------------- #

app = Flask(__name__)
CORS(app)

MODEL_NAME = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 180

# ---------------- GEMINI KEY ROTATION ---------------- #

GEMINI_KEYS = os.getenv("GEMINI_KEYS", "").split(",")
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS if k.strip()]

# 🔍 TEMP VERIFICATION (REMOVE AFTER TESTING)
print("🔍 Gemini keys loaded:", len(GEMINI_KEYS))

if not GEMINI_KEYS:
    raise RuntimeError("No Gemini API keys found in GEMINI_KEYS")

key_cycle = itertools.cycle(GEMINI_KEYS)

def get_gemini_model():
    last_error = None

    for _ in range(len(GEMINI_KEYS)):
        api_key = next(key_cycle)

        # 🔍 TEMP VERIFICATION (REMOVE AFTER TESTING)
        print("🔁 Trying Gemini key:", api_key[:6] + "****")

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
            # 🔍 TEMP VERIFICATION (REMOVE AFTER TESTING)
            print("❌ Key failed:", api_key[:6] + "****", "|", str(e))
            last_error = e
            continue

    raise RuntimeError(f"All Gemini API keys exhausted: {last_error}")

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
    "timetable": "rr.txt"
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
        if score > 0:
            scored.append((score, c["raw"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [s[1] for s in scored[:limit]]

# ---------------- CHAT ROUTE ---------------- #

@app.route("/chat", methods=["POST"])
def chat():
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

    try:
        model = get_gemini_model()
        response = model.generate_content(prompt)
        reply = response.text.strip()
    except Exception as e:
        msg = str(e).lower()
        if "quota" in msg or "limit" in msg:
            return jsonify({"error": "Service temporarily busy. Please try again later."}), 429
        return jsonify({"error": str(e)}), 500

    return jsonify({"reply": reply})

# ---------------- RUN ---------------- #

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)
