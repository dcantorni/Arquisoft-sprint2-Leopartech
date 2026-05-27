import logging
from database import engine_write, Base
import models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Creating database tables...")
    try:
        Base.metadata.create_all(bind=engine_write)
        logger.info("Database tables created successfully.")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")

if __name__ == "__main__":
    main()
