# app/crud.py
from sqlalchemy.orm import Session
from . import models
from .models import GameStatus, LogEntry, RevenueEntry, Location, Game
from typing import Optional

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

def create_location(db: Session, name: str, rows: int, columns: int, cell_size: int, token_value: float):
	db_location = Location(
		name=name,
		rows=rows,
		columns=columns,
		cell_size=cell_size,
		token_value=token_value
	)
	db.add(db_location)
	db.commit()
	db.refresh(db_location)
	return db_location

def delete_location(db: Session, location: Location):
	db.delete(location)
	db.commit()


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

def create_game(db: Session, name: str, category_id: int, location_id: Optional[int], x: Optional[int], y: Optional[int], poc_name: Optional[str], poc_email: Optional[str], poc_phone: Optional[str], icon: Optional[str]):
    db_game = Game(
        name=name,
        category_id=category_id,
        location_id=location_id,
        x=x,
        y=y,
        poc_name=poc_name,
        poc_email=poc_email,
        poc_phone=poc_phone,
        icon=icon,
        status=GameStatus.working
    )
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    return db_game


def update_game_status(db: Session, game: models.Game, status: GameStatus, user_id: int, comment: str = ""):
	game.status = status
	log = LogEntry(
		game_id=game.id,
		user_id=user_id,
		action=status.value,
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
		action=status.value,
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
		action="working",
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

# --- NEW: History Deletion ---

def clear_all_log_entries(db: Session):
    db.query(LogEntry).delete()
    db.commit()

def clear_all_revenue_entries(db: Session):
    db.query(RevenueEntry).delete()
    db.commit()