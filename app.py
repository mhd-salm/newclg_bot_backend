from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
import PyPDF2
import requests
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
TAVILY_KEY = os.getenv("TAVILY_API_KEY")

app = Flask(__name__)
CORS(app)

college_data = ""
sessions = {}

# ---------- Load PDF ----------


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


pdf_list = ["college.pdf", "bb.pdf","SHIFT 1 UG & PG.pdf"]
load_pdfs(pdf_list)






@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        message = data["message"]
        session_id = data["sessionId"]

        if session_id not in sessions:
            sessions[session_id] = []

        # Store user message
        sessions[session_id].append({"role": "user", "content": message})

        # ---- DATE & TIME DETECTION ----
        date_keywords = ["date", "today", "time", "day"]
        if any(word in message.lower() for word in date_keywords):
            from datetime import datetime
            now = datetime.now()
            current_date = now.strftime("%B %d, %Y")
            current_time = now.strftime("%I:%M %p")
            reply = f"Today's date is {current_date}, and the current time is {current_time}."
            sessions[session_id].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})


        web_keywords = ["score", "weather", "who is", "news", "match", "live", "update"]
        use_web = any(k in message.lower() for k in web_keywords)

        web_info = ""
        if use_web:
            try:
                web_info = search_web(message)
            except:
                web_info = "(Web search failed)"

        prompt = f"""
You are CampusGuide AI.

College Information:
--------------------
{college_data}

Live Web Search Results:
------------------------
{web_info}

Chat History:
{json.dumps(sessions[session_id])}

User Message: {message}

Rules:
- If the question is about college → use only PDF.
- If the question is general → use web search + your own reasoning.
- If web search fails → answer normally using LLM reasoning.
"""

    
        model = genai.GenerativeModel("models/gemini-2.0-flash")

        response = model.generate_content(prompt)
        reply = response.text

        sessions[session_id].append({"role": "assistant", "content": reply})

        return jsonify({"reply": reply})

    except Exception as e:
        print("Backend Error:", e)
        return jsonify({"reply": f"⚠ Server error: {e}"}), 500



if __name__ == "__main__":
    app.run(port=4000)
