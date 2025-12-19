from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import logging
import time  # <-- ADDED
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

# ================= STORAGE =================
raw_texts = {}
text_chunks = []
sessions = {}

# ================= FALLBACK =================
FALLBACK_QA = {
    "secretary": "Janab M. Nazar Mohamed Sahib",
    "december": """Here are the important dates in December 2025:

- December 01, Monday:
  Publication of November 2025 End-Semester Examination Results

- December 04, Thursday:
  Last date for payment of Term Fee without fine

- December 10, Wednesday:
  Session on Human Values – Shift I

- December 18, Thursday:
  Last date for payment of Term Fee with fine

- December 24, Wednesday:
  Session on Human Values – Shift II

- December 25, Thursday:
  Christmas Holiday

- December 26, Friday to December 31, Wednesday:
  Holidays
"""
,
    "courses": """The college offers the following courses:
School of Language and Literature:
- B.A. English Literature
- B.A. Arabic
- M.A. Arabic
- M.A. English
- M.A. Tamil
- B.A. Urdu
- B.A. Tamil
- Ph.D. Arabic
- Ph.D. Tamil
- Ph.D. English

School of Humanities & Social Sciences:
- B.A. Economics
- B.A. Historical Studies
- B.A. Sociology
- M.A. Economics
- B.A. Business Economics
- B.A. Defence and Strategic Studies
- B.Sc. Electronic Media
- M.A. History
- M.A. Sociology
- Ph.D. Economics
- Ph.D. History

School of Commerce & Management:
- B.Com. General
- B.Com. Corporate Secretaryship
- M.Com. General
- B.Com. Bank Management
- B.Com. Information System and Management
- B.Com. Accounting Finance
- B.Com. Professional Accounting
- M.Com. Corporate Secretaryship
- Ph.D. Commerce
- Ph.D. Corporate Secretaryship

School of Computational Sciences:
- B.Sc. Mathematics
- B.Sc. Computer Science (A, B, C, D)
- B.Sc. Information Technology
- B.C.A. Computer Applications (A, B, C)
- B.Sc. Computer Science with Artificial Intelligence
- B.Sc. Computer Science with Data Science
- M.Sc. Mathematics
- M.Sc. Computer Science
- M.Sc. Information Technology
- Ph.D. Mathematics

School of Physical & Chemical Sciences:
- B.Sc. Chemistry - CPM
- B.Sc. Chemistry - CPZ
- B.Sc. Physics (Batch 1 & 2)
- M.Sc. Chemistry
- M.Sc. Physics
- Ph.D. Chemistry
- Ph.D. Physics

School of Biological Sciences:
- B.Sc. Advanced Zoology
- B.Sc. Advanced Botany
- M.Sc. Zoology
- M.Sc. Botany
- Ph.D. Zoology
- Ph.D. Botany
""",
    "fee": """The fee structure for B.Sc. Artificial Intelligence
is as follows:

- Insurance Fee: ₹75
- General Deposit: ₹250
- Other Fee: ₹395
- Science Fee: ₹500
- Special Fee: ₹1,000
- Tuition Fee: ₹28,240
- Infrastructure and Amenities Fee: ₹4,250
- Miscellaneous Fee: ₹1,250
- Registration Fee: ₹250
- ERP Fee: ₹125
- Laboratory Fee: ₹5,000
- Maintenance Fee: ₹0
- Alumni Trust Fee: ₹250

Total Fee: ₹41,585
"""
,
    "measi": """The Muslim Educational Association of Southern India (MEASI),
the parent body of The New College, offers the following schemes:

1. MEASI Scholarship
This scholarship is awarded to poor and deserving students.

Regulations:
- Sanctioned to students of self-financing U.G. and P.G. programmes only.
- Application forms are issued and accepted only on dates notified by
  MEASI or the College office.
- Scholarships cover only current semester fees (II, IV, and VI).
  Previous semester fee dues are not covered.
- Fresh applicants must submit recommendation letters from Executive
  Committee members or Life Members of MEASI, Doctors, Advocates,
  and the Head of the Department.
- Applicants must submit an income certificate of the parent from
  their employer. Self-employed parents can obtain one from a
  MEASI Executive Committee member or a competent government department.
- Applicants must attach photocopies of:
  * Bonafide Certificate from the college
  * Fee receipts (fresh and renewal)
  * H.S.C. or +2 Marks Statement or Provisional Degree Certificate
- Senior students (II and III years) must also submit:
  * Attendance certificate for the previous year
    (minimum 85% attendance required)
  * Attested copies of examination mark statements for the previous
    year or semester (minimum 50% marks in all subjects;
    students with arrears will not be considered)
  * Evidence of applying on the National Scholarship Portal
- Applications submitted without specified enclosures will not be considered.
- MEASI scholarships are paid from Zakath funds and income from
  scholarship endowments. Only students eligible to receive Zakath
  may apply.

2. MEASI Free Meals Scheme (MEFMES)
This is a non-statutory committee.

- Coordinator (Shift I): Dr. K. Syed Suraj Babu
- Coordinator (Shift II): Dr. N. Abdul Kalam
"""
,
    "zoology": "The staff in the Zoology department are:\n"

"* Dr. M. Asrar Sheriff Associate Professor & Head of the Department, Principal\n"
"* Dr. Mohammed Ibrahim Naveed (Associate Professor & Head of the Department i/c)\n"
"* Dr. Abdus Saboor (Associate Professor)\n"
"* Dr. A. Hyder Ali (Associate Professor)\n"
"* Dr. A.K. Sultan Mohideen (Associate Professor)\n"
"* Dr. Mohamed Saquib Naveed (Assistant Professor)\n"
"* Dr. Mehraj Ud Din War (Assistant Professor)\n"
"* Lt. A. Athaullah (Assistant Professor)\n"
"* Dr. M. Jamal Mohamed (Associate Professor)\n"
"* Capt. N. Md. Azmathullah (Assistant Professor)\n"
"* Dr. M. Saiyad Musthafa (Assistant Professor)\n"
"* Dr. S. Syed Raffic Ali (Assistant Professor)\n"
"* Mr. Zoyeb Mohamed Zia (Assistant Professor)\n"
"* Dr. Nagoor Meerasa Mohammed (Management Faculty, Assistant Professor)\n",
    "principal and vice principals":
        "Principal: Dr. M. Asrar Sheriff\n"
        "Vice-Principal (Academic): Dr. P.A. Abdullah Mahaboob\n"
        "Vice-Principal (Administration): Dr. A. Hyder Ali\n"
        "Vice-Principal (Shift II): Dr. Syed Abdul Hameed",
    "vice principals":
        "Vice-Principal (Academic): Dr. P.A. Abdullah Mahaboob\n"
        "Vice-Principal (Administration): Dr. A. Hyder Ali\n"
        "Vice-Principal (Shift II): Dr. Syed Abdul Hameed",
    "coe": """The Office of the Controller of Examinations (COE) is responsible for
planning and conducting End-Semester Examinations, overseeing answer script
evaluation, result publication, and issuing mark statements and transcripts.
It also offers genuineness and verification services.

Key Personnel:
- Controller of Examinations:
  Mr. A. Syed Sarmadh Ahmed
- Deputy Controller of Examinations:
  Dr. M. Nizam Mohideen
- Assistant Controller of Examinations:
  Dr. A. Mohamed Yunus

Contact Details:
- COE Office Phone: 044-28351888
""",
    "iiec": """The Innovation, Incubation and Entrepreneurship Centre (IIEC) was initiated
with the recommendations of the Ministry of Education (Government of India)
through the Institutional Innovation Council (IIC).

The centre broadly aims at:
- Developing and sustaining an innovation ecosystem in the campus.
- Sensitising faculty members on the need to cultivate a culture of
  entrepreneurship and innovation among students.
- Identifying and nurturing entrepreneurs and innovators among students
  and staff.
- Guiding the establishment of start-ups and helping innovators and
  entrepreneurs in Intellectual Property (IP) generation and commercialization.
- Maintaining the innovation lab and the entrepreneurship promotion centre
  in the campus.
- Coordinating the activities of the Innovation and Entrepreneurship Club
  for members and students.

Director:
Dr. M. Abdul Jamal
Associate Professor of Economics
""",

    "principal":" Dr. M. Asrar Sheriff",
    "iqac": """The Internal Quality Assurance Cell (IQAC) oversees the overall
quality initiatives of the college.

Responsibilities:
- Formulating annual strategic plans
- Preparing the college for NAAC accreditation
- Submitting data for the National Institutional Ranking Framework (NIRF)
  and All India Survey of Higher Education (AISHE)
- Handling the extension of Autonomous status
- Collecting and analyzing feedback from stakeholders
- Conducting academic and administrative audits
- Promoting online learning through MOOC courses

Structure:
The IQAC is led by a Director, Deputy Director, and Coordinators.
Supporting committees include:
- Steering Committee
- Data Coordination Team
- Departmental IQAC Aides (Micro Cells)

Current Leadership:
- Director:
  Dr. Anvar Sadhath Valiyaparambath
- Deputy Director:
  Dr. T. Abdul Khadar
- Coordinators:
  Dr. U. Muhammed Rafi
  Dr. Syed Shakir Razvi
"""
,
    "hostel": """The New College Hostel provides accommodation for boys enrolled
in the college and has a capacity of over 550 students.

Facilities and Amenities:
- Comfortable, spacious, and well-furnished rooms
- Complimentary laundry service
- Hygienically prepared meals and R.O. drinking water
- Peaceful environment conducive to studying
- Separate section for postgraduate students
- Green initiatives including:
  * Biogas plant
  * Grey water recycling plant
  * Organic vegetable garden
  * RO water plant
- In 2025, an additional floor was added to the Entrance Block

Leadership and Staff:
- Warden:
  Dr. M. Asrar Sheriff (Principal)
- Deputy Warden:
  Dr. S. Tameem Sharief
- Resident Superintendents:
  Dr. M. Mohamed Suhail
  Dr. Mohammed Rahamtulla
- Assistant Manager:
  Mr. M. Imthias Ahamed
- Accountant:
  Mr. A. Abdhul Shukur
- Office Assistants:
  Mr. S. M. Sulthan Arif
  Mr. Mohammed Anwar Ahamed
- Plumber cum Electrician:
  Mr. H. Mohamed Saleem

Rules and Regulations:
- Strict discipline must be maintained
- Students are not permitted to remain in the hostel during class hours
  without prior permission from the Deputy Warden
- Guests and day scholars are not allowed inside hostel premises or rooms
- Meals must be taken within the specified mess timings
- Designated study hours are from 9:30 PM to 10:30 PM
- Boarders are not permitted to leave the hostel premises between
  9:00 PM and 5:00 AM without written permission
- Entry after 9:00 PM is not permitted
- Students going to their hometown must submit a leave application
  signed by the respective Head of Department and Resident Superintendent
- Guests may be met only in the designated guest room
- Students must keep valuables safely; the management is not responsible
  for loss or theft
- Transfer Certificates, mark statements, and other documents will be
  issued only after producing a "NO DUES" certificate from the hostel
- Boarders must treat hostel staff with respect
- Genuine grievances should be submitted in writing to the Deputy Warden
- Penalties will be imposed for misuse of electricity, water, or facilities
- Boarders are responsible for damages to furniture or fittings
- Political activities are strictly prohibited within the hostel premises
- The college reserves the right to cancel hostel membership without notice

Admissions:
- Hostel admission for II & III UG and II PG students begins on 16.06.2025

Contact Details:
- Phone: 044-28352686
"""

}

def fallback_answer(msg: str):
    msg_lower = msg.lower()
    for key in FALLBACK_QA:
        if key in msg_lower:
            return FALLBACK_QA[key]
    return None

# ================= TEXT LOADING ============
def normalize_text(text: str) -> str:
    text = text.replace("\t", " ")
    text = re.sub(r" +", " ", text)
    return text.strip()

def split_into_chunks(text: str):
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
- Do NOT guess or calculate

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

        if lower in {"date", "time", "today"}:
            reply = f"Today is {now.strftime('%B %d, %Y')} ({now.strftime('%I:%M %p')})."
            sessions[sid].append({"role": "assistant", "content": reply})
            return jsonify({"reply": reply})

        if "day order" in lower or "timetable" in lower:
            target = parse_date(lower, now)
            order = get_day_order(target)
            if order is None:
                reply = "Sunday has no classes or timetable."
                sessions[sid].append({"role": "assistant", "content": reply})
                return jsonify({"reply": reply})

        context = retrieve_relevant_chunks(msg)
        reply = None
        used_fallback = False

        if context:
            history = compact_history(sessions[sid])
            prompt = build_prompt(context, history, msg)
            try:
                model = genai.GenerativeModel("models/gemini-2.5-flash-lite")
                res = model.generate_content(prompt)
                reply = getattr(res, "text", None)
            except Exception as e:
                logger.warning("Gemini failed, switching to fallback: %s", e)

        if not reply:
            reply = fallback_answer(msg) or "Not available in college records."
            used_fallback = True

        # ⏳ DELAY ONLY FOR FALLBACK
        if used_fallback:
            time.sleep(1.5)

        sessions[sid].append({"role": "assistant", "content": reply})
        sessions[sid] = sessions[sid][-MAX_HISTORY * 2:]

        return jsonify({"reply": reply})

    except Exception as e:
        logger.exception("Chat error")
        reply = fallback_answer(msg) or "Server error occurred."
        time.sleep(1.5)  # delay for fallback on crash
        return jsonify({"reply": reply}), 500

@app.route("/reload_texts", methods=["POST"])
def reload_texts():
    load_text_files(TEXT_FILES)
    return jsonify({"ok": True, "chunks": len(text_chunks)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT)
