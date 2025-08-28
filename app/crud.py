# app/crud.py
from sqlalchemy.orm import Session
from app import models                              # 🔽 absolute
from app.models import GameStatus, LogEntry, RevenueEntry, Location, Game, User, UserRole
from typing import Optional


# ----------- USERS -----------

def get_user_by_pin(db: Session, pin: str):
	return db.query(models.User).filter(models.User.pin == pin).first()

def get_users(db: Session):
	return db.query(models.User).all()

def get_user_by_id(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()

def create_user(db: Session, name: str, pin: str, role: UserRole,
                email: Optional[str] = None,
                phone: Optional[str] = None,
                notify: bool = False):
    db_user = User(name=name, pin=pin, role=role,
                   email=(email or None),
                   phone=(phone or None),
                   notify=bool(notify))
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(db: Session, user: User, name: str, pin: Optional[str], role: UserRole,
                email: Optional[str] = None,
                phone: Optional[str] = None,
                notify: Optional[bool] = None):
    user.name = name
    user.role = role
    if pin:
        user.pin = pin
    user.email = (email or None)
    user.phone = (phone or None)
    if notify is not None:
        user.notify = bool(notify)
    db.commit()
    db.refresh(user)
    return user

def get_users_to_notify(db: Session):
    return (db.query(User)
              .filter(User.notify == True, User.email.isnot(None))  # noqa: E712
              .all())

def delete_user(db: Session, user_to_delete: User):
    db.delete(user_to_delete)
    db.commit()

# ----------- LOCATIONS -----------

def get_locations(db: Session):
    return db.query(models.Location).order_by(models.Location.name).all()

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
    return db.query(models.Category).order_by(models.Category.name).all()

def get_category_by_id(db: Session, category_id: int):
    return db.query(models.Category).filter(models.Category.id == category_id).first()

def get_category_by_name(db: Session, name: str):
    return db.query(models.Category).filter(models.Category.name == name).first()

def create_category(db: Session, name: str, icon: str | None = None):
    cat = models.Category(name=name.strip(), icon=icon or None)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat

def update_category(db: Session, category: models.Category, name: str, icon: str | None = None):
    category.name = name.strip()
    category.icon = icon or None
    db.commit()
    db.refresh(category)
    return category

def delete_category(db: Session, category: models.Category):
    db.delete(category)
    db.commit()

# ----------- GAMES -----------

def get_games_by_location(db: Session, location_id: int):
    return db.query(models.Game).filter(models.Game.location_id == location_id).all()

def get_game_by_id(db: Session, game_id: int):
    return db.query(models.Game).filter(models.Game.id == game_id).first()

def get_game_at(db: Session, location_id: int, x: int, y: int):
    return (db.query(models.Game)
              .filter(models.Game.location_id == location_id,
                      models.Game.x == x,
                      models.Game.y == y)
              .first())

def update_game_position(db: Session, game: models.Game, x: int, y: int):
    game.x = x
    game.y = y
    db.commit()
    db.refresh(game)
    return game

def swap_game_positions(db: Session, game_a: models.Game, game_b: models.Game):
    ax, ay = game_a.x, game_a.y
    bx, by = game_b.x, game_b.y
    game_a.x, game_a.y = bx, by
    game_b.x, game_b.y = ax, ay
    db.commit()
    db.refresh(game_a)
    db.refresh(game_b)
    return game_a, game_b

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

from typing import Optional
from datetime import datetime

def log_revenue(
    db: Session,
    game: models.Game,
    user_id: int,
    amount: float,
    is_token: bool,
    collected_at: Optional[datetime] = None
):
    entry = RevenueEntry(
        game_id=game.id,
        user_id=user_id,
        amount=amount,
        is_token=is_token
    )
    if collected_at:
        entry.timestamp = collected_at  # override server_default now

    db.add(entry)
    db.commit()
    return entry

# --- History Deletion ---

def clear_all_log_entries(db: Session):
    db.query(LogEntry).delete()
    db.commit()

def clear_all_revenue_entries(db: Session):
    db.query(RevenueEntry).delete()
    db.commit()

# crud.py
def clear_status_history_for_game(db: Session, game_id: int):
    # Replace models.StatusEntry with your actual status-history model/table
    db.query(models.StatusEntry).filter(models.StatusEntry.game_id == game_id).delete(synchronize_session=False)
    db.commit()

def clear_revenue_history_for_game(db: Session, game_id: int):
    # If your table is revenue_entries, update accordingly
    db.query(models.RevenueEntry).filter(models.RevenueEntry.game_id == game_id).delete(synchronize_session=False)
    db.commit()
