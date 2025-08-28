# app/models.py
from sqlalchemy import Column, Integer, String, ForeignKey, Enum, DateTime, Float, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum

class UserRole(str, enum.Enum):
	admin = "admin"
	user = "user"

class GameStatus(str, enum.Enum):
	working = "working"
	needs_maintenance = "needs_maintenance"
	out_of_order = "out_of_order"

class Settings(Base):
	__tablename__ = "settings"

	id = Column(Integer, primary_key=True, index=True)
	grid_rows = Column(Integer, default=5)
	grid_columns = Column(Integer, default=5)
	cell_size = Column(Integer, default=150)  # pixels
	token_value = Column(Float, default=1.0)  # real money per token
	background_image = Column(String, nullable=True)  # path to bg image

class User(Base):
	__tablename__ = "users"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String, nullable=False)
	pin = Column(String(4), nullable=False)
	role = Column(Enum(UserRole), default=UserRole.user)
	email = Column(String, nullable=True)
	phone = Column(String, nullable=True)
	notify = Column(Boolean, default=False)

	logs = relationship("LogEntry", back_populates="user")
	revenue_entries = relationship("RevenueEntry", back_populates="user")

class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    # Grid configuration
    rows = Column(Integer, default=5)
    columns = Column(Integer, default=5)
    cell_size = Column(Integer, default=80)   # px
    token_value = Column(Float, default=1.0)  # money per token
    background_image = Column(String, nullable=True)

    games = relationship("Game", back_populates="location")

class Category(Base):
	__tablename__ = "categories"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String, nullable=False, unique=True)
	icon = Column(String, nullable=True)

	games = relationship("Game", back_populates="category")

class Game(Base):
	__tablename__ = "games"

	id = Column(Integer, primary_key=True, index=True)
	name = Column(String, nullable=False)
	status = Column(Enum(GameStatus), default=GameStatus.working)
	x = Column(Integer)
	y = Column(Integer)
	icon = Column(String, nullable=True)       # small icon
	cabinet_pic = Column(String, nullable=True) # larger cabinet picture
	poc_name = Column(String, nullable=True)
	poc_email = Column(String, nullable=True)
	poc_phone = Column(String, nullable=True)

	category_id = Column(Integer, ForeignKey("categories.id"))
	# --- MODIFIED --- Made location_id nullable
	location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)

	category = relationship("Category", back_populates="games")
	location = relationship("Location", back_populates="games")
	logs = relationship("LogEntry", back_populates="game")
	revenue_entries = relationship("RevenueEntry", back_populates="game")

class LogEntry(Base):
	__tablename__ = "log_entries"

	id = Column(Integer, primary_key=True, index=True)
	timestamp = Column(DateTime(timezone=True), server_default=func.now())
	action = Column(String)
	comments = Column(String, nullable=True)

	user_id = Column(Integer, ForeignKey("users.id"))
	game_id = Column(Integer, ForeignKey("games.id"))

	user = relationship("User", back_populates="logs")
	game = relationship("Game", back_populates="logs")

class RevenueEntry(Base):
	__tablename__ = "revenue_entries"

	id = Column(Integer, primary_key=True, index=True)
	timestamp = Column(DateTime(timezone=True), server_default=func.now())
	amount = Column(Float, nullable=False)
	is_token = Column(Boolean, default=False)

	user_id = Column(Integer, ForeignKey("users.id"))
	game_id = Column(Integer, ForeignKey("games.id"))

	user = relationship("User", back_populates="revenue_entries")
	game = relationship("Game", back_populates="revenue_entries")