import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

logger = logging.getLogger(__name__)

# Format URLs
def get_db_url(host: str) -> str:
    return f"postgresql://{settings.DATABASE_USER}:{settings.DATABASE_PASSWORD}@{host}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"

try:
    write_url = get_db_url(settings.DATABASE_HOST)
    engine_write = create_engine(write_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    SessionLocalWrite = sessionmaker(autocommit=False, autoflush=False, bind=engine_write)
except Exception as e:
    logger.error(f"Failed to connect to write database: {e}")
    engine_write = None
    SessionLocalWrite = None

try:
    read_url = get_db_url(settings.DATABASE_READ_HOST)
    engine_read = create_engine(read_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    SessionLocalRead = sessionmaker(autocommit=False, autoflush=False, bind=engine_read)
except Exception as e:
    logger.error(f"Failed to connect to read database: {e}")
    engine_read = None
    SessionLocalRead = None

Base = declarative_base()

# FastAPI Dependencies
def get_write_db():
    db = SessionLocalWrite()
    try:
        yield db
    finally:
        db.close()

def get_read_db():
    db = SessionLocalRead()
    try:
        yield db
    finally:
        db.close()
