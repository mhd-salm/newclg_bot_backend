import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import google.generativeai as genai

# ---------------- BASIC CONFIG ---------------- #

app = Flask(__name__)
CORS(app)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL_NAME = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 180

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
            parts = re.split(r"\n\s*\n", text)  # paragraph chunks
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

    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config={
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0.2
        }
    )

    try:
        response = model.generate_content(prompt)
        reply = response.text.strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"reply": reply})

# ---------------- RUN ---------------- #

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)
