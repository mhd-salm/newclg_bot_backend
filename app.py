"""
app.py
──────
Application entry point.

What changed vs the original
─────────────────────────────
1. Extensions (db, bcrypt, jwt, limiter) are now initialised via
   init_app() from extensions.py — no logic moved, just wiring.
2. Auth Blueprint registered at /auth.
3. /chat route now requires a valid JWT (@jwt_required).
4. Student context (department + year) is injected into the prompt
   ONLY for timetable/schedule queries — no extra tokens otherwise.
5. Database tables are created on first run via create_all().

Everything else — Gemini logic, chunk selection, date injection,
prompt structure, rate limiting, CORS — is IDENTICAL to the original.
"""
from models import Student
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
from database import db
import os
import re
import sys
import logging
from datetime import datetime

from flask        import Flask, request, jsonify
from flask_cors   import CORS
from flask_jwt_extended import jwt_required, get_jwt_identity
from dotenv       import load_dotenv

import google.generativeai as genai

# ── Local modules ──────────────────────────────────────────
from config     import config_map
from extensions import db, bcrypt, jwt, limiter
from models     import Student
from auth       import auth_bp



# ══════════════════════════════════════════════════════════
#  Application Factory
# ══════════════════════════════════════════════════════════

def create_app(env: str = None) -> Flask:
    load_dotenv()
    
    env = env or os.getenv("FLASK_ENV", "default")
    app = Flask(__name__)

    # Load configuration FIRST
    app.config.from_object(config_map[env])

    # Override using .env AFTER load_dotenv()
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")
    app.config["GEMINI_KEYS"] = os.getenv("GEMINI_KEYS")

    # THEN initialize extensions
    db.init_app(app)
    # ── Logging ───────────────────────────────────────────
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s | %(levelname)s | %(message)s",
        handlers= [logging.StreamHandler(sys.stdout)],
    )
    app.logger.setLevel(logging.INFO)

    # ── Extensions ────────────────────────────────────────
    
    bcrypt.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)


    # ── CORS  (only /chat and /auth origins allowed) ───────
    
    CORS(
    app,
    resources={r"/chat": {"origins": "*"},
               r"/auth/*": {"origins": "*"}},
    supports_credentials=True
    )
    # ── Auth Blueprint ────────────────────────────────────
    app.register_blueprint(auth_bp, url_prefix="/auth")

    # ── Create DB tables if they don't exist ─────────────
    with app.app_context():
        db.create_all()
        app.logger.info("✅ Database tables verified / created.")

    # ── Register chat route (defined below) ───────────────
    _register_chat_route(app)

    return app


# ══════════════════════════════════════════════════════════
#  Gemini Key Failover  (UNCHANGED from original)
# ══════════════════════════════════════════════════════════

# These are module-level so they survive across requests.
_gemini_keys:       list[str] = []
_current_key_index: int       = 0

MODEL_NAME        = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 180


def _load_gemini_keys(app: Flask):
    global _gemini_keys
    raw = app.config.get("GEMINI_KEYS", "")
    _gemini_keys = [k.strip() for k in raw.split(",") if k.strip()]
    app.logger.info(f"🔍 Gemini keys loaded: {len(_gemini_keys)}")
    if not _gemini_keys:
        raise RuntimeError("No Gemini API keys found in GEMINI_KEYS")


def _get_gemini_model():
    """UNCHANGED logic — sticky key with round-robin failover."""
    global _current_key_index

    attempts   = 0
    last_error = None

    while attempts < len(_gemini_keys):
        api_key    = _gemini_keys[_current_key_index]
        key_number = _current_key_index + 1

        try:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(
                model_name        = MODEL_NAME,
                generation_config = {
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "temperature":       0.2,
                },
            )
        except Exception as e:
            import logging as _log
            _log.warning(f"❌ Gemini Key-{key_number} failed. Switching key.")
            last_error = e
            _current_key_index += 1
            attempts += 1
            if _current_key_index >= len(_gemini_keys):
                _log.info("🔁 All keys exhausted. Resetting to Key-1")
                _current_key_index = 0
                break

    raise RuntimeError(f"All Gemini API keys failed: {last_error}")


# ══════════════════════════════════════════════════════════
#  System Prompt  (UNCHANGED)
# ══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are the official chatbot of The New College, Chennai.
When the user says "our college" or "newcollege", they mean The New College, Chennai.
Answer briefly, clearly, and factually.
"""


# ══════════════════════════════════════════════════════════
#  Data Files & Chunk Loading  (UNCHANGED)
# ══════════════════════════════════════════════════════════

DATA_FILES = {
    "college":    "college.txt",
    "shift1":     "shift1.txt",
    "shift2":     "shift2.txt",
    "timetable":  "rr.txt",
    "developers": "dev.txt",
}

def _load_chunks():
    chunks = []
    for tag, file in DATA_FILES.items():
        if not os.path.exists(file):
            continue
        with open(file, "r", encoding="utf-8") as f:
            text  = f.read()
            parts = re.split(r"\n\s*\n", text)
            for p in parts:
                p = p.strip()
                if len(p) > 40:
                    chunks.append({"tag": tag, "text": p.lower(), "raw": p})
    return chunks

CHUNKS = _load_chunks()


# ══════════════════════════════════════════════════════════
#  Intent Keywords  (UNCHANGED)
# ══════════════════════════════════════════════════════════

INTENT_KEYWORDS = {
    "developers": [
        "who made", "who created", "developer", "built you",
        "salman", "mustansir", "shahid", "sathya",
    ],
    "timetable": [
        "timetable", "time table", "schedule", "period",
        "day order", "dayorder", "today", "tomorrow",
        "monday", "tuesday", "wednesday", "thursday", "friday",
    ],
    "shift1": [
        "shift 1", "shift one", "morning",
        "fee", "fees", "fee structure", "how much",
    ],
    "shift2": [
        "shift 2", "shift two", "evening",
        "fee", "fees", "fee structure", "how much",
    ],
    "college": [
        "college", "new college", "newcollege",
        "about", "history", "principal",
        "address", "contact", "department", "course",
    ],
}


def _select_relevant_chunks(user_msg: str, limit: int = 3) -> list[str]:
    """UNCHANGED chunk-selection logic."""
    user_msg_l    = user_msg.lower()
    keywords      = user_msg_l.split()
    scored        = []
    detected_tags = {
        tag for tag, words in INTENT_KEYWORDS.items()
        if any(w in user_msg_l for w in words)
    }

    for c in CHUNKS:
        if detected_tags and c["tag"] not in detected_tags:
            continue
        score = sum(1 for k in keywords if k in c["text"])

        intent_words = INTENT_KEYWORDS.get(c["tag"], [])
        if any(w in user_msg_l for w in intent_words):
            score += 50
        if c["tag"] == "developers" and any(
            k in user_msg_l for k in [
                "who made", "who created", "developer", "built you",
                "salman", "mustansir", "shahid", "sathya",
            ]
        ):
            score += 100

        if score > 0:
            scored.append((score, c["raw"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [s[1] for s in scored[:limit]]


# ══════════════════════════════════════════════════════════
#  Date Context  (UNCHANGED)
# ══════════════════════════════════════════════════════════

_DATE_KEYWORDS = [
    "today", "tomorrow", "timetable", "schedule",
    "day order", "dayorder",
    "monday", "tuesday", "wednesday", "thursday", "friday",
]

def _needs_date_context(user_msg: str) -> bool:
    msg = user_msg.lower()
    return any(k in msg for k in _DATE_KEYWORDS)

def _get_current_date_context() -> str:
    now = datetime.now()
    return f"Today is {now.strftime('%A')}, {now.strftime('%Y-%m-%d')}."


# ══════════════════════════════════════════════════════════
#  Student Personalization  (NEW — minimal token impact)
# ══════════════════════════════════════════════════════════

def _needs_student_context(user_msg: str) -> bool:
    """
    Inject student info ONLY for schedule-related queries.
    Avoids wasting tokens for normal questions.
    """
    msg = user_msg.lower()

    keywords = [
        "timetable", "time table", "schedule",
        "day order", "dayorder",
        "period", "class today",
        "what do i have", "today class",
        "lab today", "today's class"
    ]

    return any(k in msg for k in keywords)

def _get_student_context(student: Student) -> str:
    """
    One-line compact personalization.
    Keeps token usage extremely low.
    """
    now = datetime.now()

    return (
        f"Student: {student.name}, "
        f"Dept: {student.department}, "
        f"Year: {student.year}, "
        f"RegNo: {student.register_number}. "
        f"Today: {now.strftime('%A')} ({now.strftime('%Y-%m-%d')})."
    )


# ══════════════════════════════════════════════════════════
#  Chat Route  (protected by JWT, personalization added)
# ══════════════════════════════════════════════════════════

def _register_chat_route(app: Flask):
    """
    Register /chat inside the factory so it shares app context.
    The route is functionally identical to the original EXCEPT:
      • @jwt_required() — caller must send a valid access token
      • Student record fetched for optional prompt injection
    """
    # Load Gemini keys now that app config is ready
    with app.app_context():
        _load_gemini_keys(app)

    @app.route("/chat", methods=["POST"])
    @jwt_required()                          # ← NEW: requires Authorization header
    @limiter.limit("15 per minute")
    def chat():
        global _current_key_index

        # ── Identify caller ───────────────────────────────
        student_id = int(get_jwt_identity())
        student    = db.session.get(Student, student_id)
        if not student:
            return jsonify({"error": "Authenticated user not found."}), 401

        # ── Parse request ─────────────────────────────────
        data     = request.json
        user_msg = (data or {}).get("message", "").strip()
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        # ── Build prompt (ORIGINAL structure preserved) ───
        relevant_chunks = _select_relevant_chunks(user_msg)
        prompt = [SYSTEM_PROMPT.strip()]

        # Inject student personalisation ONLY for timetable queries
        if _needs_student_context(user_msg):
            prompt.append(_get_student_context(student))
        elif _needs_date_context(user_msg):
            # For other date-related queries keep original date context
            prompt.append(_get_current_date_context())

        if relevant_chunks:
            prompt.append("Relevant college information:")
            for c in relevant_chunks:
                prompt.append(c)

        prompt.append(f"User question: {user_msg}")

        # ── Gemini with key failover (UNCHANGED) ──────────
        tried_keys = 0
        last_error = None

        while tried_keys < len(_gemini_keys):
            try:
                app.logger.info(f"🚀 Trying Gemini Key-{_current_key_index + 1}")
                model    = _get_gemini_model()
                response = model.generate_content(prompt)
                return jsonify({"reply": response.text.strip()})

            except Exception as e:
                app.logger.warning("⚠️ Gemini request failed.")
                msg        = str(e).lower()
                last_error = e

                if "quota" in msg or "limit" in msg or "429" in msg:
                    app.logger.info("⚠️ Quota hit. Switching key…")
                    _current_key_index = (_current_key_index + 1) % len(_gemini_keys)
                    tried_keys += 1
                    continue

                if "invalid key" in msg or "network" in msg:
                    app.logger.info("⚠️ Recoverable error. Switching key…")
                    _current_key_index = (_current_key_index + 1) % len(_gemini_keys)
                    tried_keys += 1
                    continue

                return jsonify({"error": str(e)}), 500

        return jsonify({
            "error": "All Gemini API keys are rate-limited. Please try again later."
        }), 429


# ══════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════

app = create_app()
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    app.run(host="0.0.0.0", port=port)


