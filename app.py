from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import PyPDF2
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime

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

pdf_list = ["college.pdf", "shift1.pdf","shift2.pdf","DailyTT.pdf"]
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

        # ---- Date/Time Detection ----
        if any(k in message.lower() for k in ["date", "today", "time", "day"]):
            now = datetime.now()
            reply = (
                f"Today's date is {now.strftime('%B %d, %Y')}, "
                f"and the current time is {now.strftime('%I:%M %p')}."
            )
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




