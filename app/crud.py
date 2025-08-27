# app/crud.py
from sqlalchemy.orm import Session
from . import models
from .models import GameStatus, LogEntry, RevenueEntry

# ----------- USERS -----------

def get_user_by_pin(db: Session, pin: str):
	return db.query(models.User).filter(models.User.pin == pin).first()

def get_users(db: Session):
	return db.query(models.User).all()

# ----------- LOCATIONS -----------

def get_locations(db: Session):
	return db.query(models.Location).all()

def get_location_by_id(db: Session, location_id: int):
	return db.query(models.Location).filter(models.Location.id == location_id).first()

# ----------- CATEGORIES -----------

def get_categories(db: Session):
	return db.query(models.Category).all()

# ----------- GAMES -----------

def get_games_by_location(db: Session, location_id: int):
	return db.query(models.Game).filter(models.Game.location_id == location_id).all()

def get_game_by_id(db: Session, game_id: int):
	return db.query(models.Game).filter(models.Game.id == game_id).first()

def get_all_games(db: Session):
	return db.query(models.Game).order_by(models.Game.name).all()


def update_game_status(db: Session, game: models.Game, status: GameStatus, user_id: int, comment: str = ""):
	game.status = status
	log = LogEntry(
		game_id=game.id,
		user_id=user_id,
		action="status_change",
		comments=comment
	)
	db.add(log)
	db.commit()
	db.refresh(game)
	return game

def report_fault(db: Session, game: models.Game, user_id: int, comment: str, status: GameStatus):
	log = LogEntry(
		game_id=game.id,
		user_id=user_id,
		action="fault",
		comments=comment
	)
	game.status = status
	db.add(log)
	db.commit()
	db.refresh(game)
	return game

def report_fix(db: Session, game: models.Game, user_id: int, comment: str = ""):
	log = LogEntry(
		game_id=game.id,
		user_id=user_id,
		action="fix",
		comments=comment
	)
	game.status = GameStatus.working
	db.add(log)
	db.commit()
	db.refresh(game)
	return game

# ----------- REVENUE -----------

def log_revenue(db: Session, game: models.Game, user_id: int, amount: float, is_token: bool, period: str = ""):
	entry = RevenueEntry(
		game_id=game.id,
		user_id=user_id,
		amount=amount,
		is_token=is_token,
		period=period
	)
	db.add(entry)
	db.commit()
	return entry
