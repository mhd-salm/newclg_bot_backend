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
6. Day-order questions are answered deterministically from rr.txt
   (department + year from the logged-in student).

Everything else — Gemini logic, chunk selection, date injection,
prompt structure, rate limiting, CORS — is IDENTICAL to the original.

Token / cost (Gemini free tier, Render):
- Optional env: GEMINI_CHUNK_MAX_CHARS (default 1400), GEMINI_RETRIEVAL_CHUNKS (default 2),
  GEMINI_MAX_OUTPUT_TOKENS (default 180).
"""
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
from database import db
import os
import re
import sys
import logging
from datetime import datetime, date, timedelta

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

MODEL_NAME = "gemini-2.5-flash-lite"

# Input/output caps — override on Render via env to stay within free-tier limits
def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


GEMINI_CHUNK_MAX_CHARS = _int_env("GEMINI_CHUNK_MAX_CHARS", 1400)
GEMINI_RETRIEVAL_CHUNKS = _int_env("GEMINI_RETRIEVAL_CHUNKS", 2)
MAX_OUTPUT_TOKENS = _int_env("GEMINI_MAX_OUTPUT_TOKENS", 180)


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
#  System Prompt  (short — fewer input tokens)
# ══════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a concise factual assistant for The New College, Chennai. "
    '"newcollege" means this college. Be brief.'
)


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
    # Do not include broad words like "today"/weekdays here — they pull huge rr.txt
    # chunks and burn tokens on unrelated questions.
    "timetable": [
        "timetable", "time table", "schedule", "period",
        "day order", "dayorder", "class", "classes", "lecture",
        "what do i have", "what do we have",
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


def _trim_chunk_for_prompt(raw: str, max_chars: int | None = None) -> str:
    """Cap long text (e.g. rr.txt) so retrieval stays within free-tier limits."""
    cap = max_chars if max_chars is not None else GEMINI_CHUNK_MAX_CHARS
    if len(raw) <= cap:
        return raw
    return raw[: cap - 3].rstrip() + "..."


def _select_relevant_chunks(user_msg: str, limit: int | None = None) -> list[str]:
    """Score chunks; return top-N trimmed strings (N from GEMINI_RETRIEVAL_CHUNKS)."""
    if limit is None:
        limit = GEMINI_RETRIEVAL_CHUNKS
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
    return [_trim_chunk_for_prompt(s[1]) for s in scored[:limit]]


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
    return f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})."


# ══════════════════════════════════════════════════════════
#  Day order calendar (from rr.txt date table — deterministic)
# ══════════════════════════════════════════════════════════

_DAY_ORDER_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}

# Parsed once: ISO date string -> "holiday" | int (day order 1–6)
_day_order_calendar: dict[str, object] | None = None

_RR_DATE_LINE_RE = re.compile(
    r"^(\d{2})-(\d{2})-(\d{4})\s+(\w+)\s+(.+)$"
)


def _load_day_order_calendar() -> dict[str, object]:
    """
    Parse the leading date table in rr.txt (DD-MM-YYYY ... Holiday|number).
    Cached at module level after first load.
    """
    global _day_order_calendar
    if _day_order_calendar is not None:
        return _day_order_calendar

    path = DATA_FILES.get("timetable", "rr.txt")
    cal: dict[str, object] = {}

    if not os.path.exists(path):
        _day_order_calendar = cal
        return cal

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            m = _RR_DATE_LINE_RE.match(line)
            if not m:
                continue

            dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
            rest = m.group(5).strip().strip("`").strip()

            iso = f"{yyyy}-{mm}-{dd}"

            if rest.lower().startswith("holiday"):
                cal[iso] = "holiday"
                continue

            num_match = re.search(r"(\d+)", rest)
            if num_match:
                n = int(num_match.group(1))
                if 1 <= n <= 6:
                    cal[iso] = n

    _day_order_calendar = cal
    return cal


def _get_day_order_for_iso(iso: str) -> tuple[str, int | None]:
    """
    Returns (kind, value) where kind is 'number'|'holiday'|'missing'.
    value is 1–6 when kind == 'number'.
    """
    cal = _load_day_order_calendar()
    entry = cal.get(iso)
    if entry == "holiday":
        return "holiday", None
    if isinstance(entry, int):
        return "number", entry
    return "missing", None


def _weekday_index(name: str) -> int | None:
    n = name.lower()[:3]
    mapping = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }
    return mapping.get(n)


def _resolve_target_date_for_day_order(user_msg: str) -> date | None:
    """
    Resolve which calendar date the user means (today / tomorrow / weekday / DD-MM-YYYY).
    """
    m = user_msg.lower()
    today = datetime.now().date()

    if "today" in m:
        return today
    if "tomorrow" in m:
        return today + timedelta(days=1)

    dm = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", user_msg)
    if dm:
        d, mo, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    for w in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        if w in m:
            idx = _weekday_index(w)
            if idx is None:
                continue
            delta = (idx - today.weekday()) % 7
            return today + timedelta(days=delta)

    # Implicit "today" for clear day-order questions without another date
    if re.search(r"\b(day order|dayorder)\b", m) and not re.search(
        r"\b\d{2}-\d{2}-\d{4}\b", user_msg
    ):
        if (
            re.search(r"\b(what|which|when|how)\b.*\b(day order|dayorder)\b", m)
            or re.search(r"\b(day order|dayorder)\b.*\b(what|which|when|how)\b", m)
            or re.search(r"\b(day order|dayorder)\b.*\b(today|now|current)\b", m)
            or re.search(r"\b(today|now|current)\b.*\b(day order|dayorder)\b", m)
        ):
            return today

    return None


def _is_deterministic_day_order_query(user_msg: str) -> bool:
    """
    True when the user asks only for day order (not full timetable/schedule).
    """
    m = user_msg.lower()
    if "day order" not in m and "dayorder" not in m:
        return False
    # Combined timetable questions: let Gemini use chunks + student context
    if any(
        k in m
        for k in (
            "timetable",
            "time table",
            "schedule",
            "period",
            "class",
            "classes",
            "what do i have",
            "lab",
        )
    ):
        return False
    return _resolve_target_date_for_day_order(user_msg) is not None


def _format_day_order_reply(student: Student, target: date) -> str:
    """
    Build a deterministic reply using rr.txt calendar + logged-in student dept/year.
    """
    iso = target.strftime("%Y-%m-%d")
    weekday = target.strftime("%A")
    kind, num = _get_day_order_for_iso(iso)

    dept = (student.department or "").strip() or "your department"
    yr = student.year

    if kind == "holiday":
        return (
            f"For {dept}, Year {yr}: {weekday}, {iso} is a holiday in the published "
            f"calendar — there is no day order on that date."
        )

    if kind == "missing":
        return (
            f"For {dept}, Year {yr}: there is no day order entry in the calendar data "
            f"for {weekday}, {iso}. Check with your department for updates."
        )

    roman = _DAY_ORDER_ROMAN.get(num or 0, str(num))
    return (
        f"For {dept}, Year {yr}: {weekday}, {iso} is Day Order {roman} (day order {num})."
    )


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
    """Minimal line for schedule queries (name/reg no omitted to save tokens)."""
    now = datetime.now()
    dept = (student.department or "").strip() or "—"
    return (
        f"User dept: {dept}; year: {student.year}. "
        f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})."
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

        # ── Deterministic day order (from rr.txt; uses logged-in dept/year) ───
        if _is_deterministic_day_order_query(user_msg):
            target_date = _resolve_target_date_for_day_order(user_msg)
            if target_date:
                return jsonify({"reply": _format_day_order_reply(student, target_date)})

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
            prompt.append("Context:")
            for c in relevant_chunks:
                prompt.append(c)

        prompt.append(f"Q: {user_msg}")

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


