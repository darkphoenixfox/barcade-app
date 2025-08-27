# app/database.py
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Define DB path (inside container or local env)
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./barcade.db")

# For SQLite, check_same_thread must be False when using multithreaded apps like FastAPI
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency for route functions
def get_db():
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()
