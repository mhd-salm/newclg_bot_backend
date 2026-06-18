from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Database
db = SQLAlchemy()

# Rate limiting
limiter = Limiter(
    key_func=get_remote_address
)