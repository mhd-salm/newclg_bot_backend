from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Database
db = SQLAlchemy()

# Password hashing
bcrypt = Bcrypt()

# JWT Authentication
jwt = JWTManager()

# Rate limiting
limiter = Limiter(
    key_func=get_remote_address
)