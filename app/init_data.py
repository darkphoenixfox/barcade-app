# app/init_data.py
from sqlalchemy.orm import Session
from .database import Base, engine, SessionLocal
from .models import User, UserRole, Location, Category, Game, GameStatus, LogEntry, RevenueEntry

def init_demo_data():
	Base.metadata.drop_all(bind=engine)
	Base.metadata.create_all(bind=engine)

	db: Session = SessionLocal()

	# Users
	alice = User(name="Alice", pin="1234", role=UserRole.management, email="alice@barcade.com", notify=True)
	bob = User(name="Bob", pin="5678", role=UserRole.staff, email="bob@barcade.com", notify=False)
	db.add_all([alice, bob])

	# Location
	main_room = Location(name="Main Room", rows=5, columns=5, background_image=None)
	db.add(main_room)

	# Category
	pinball = Category(name="Pinball", icon="/static/images/pinball.png")
	db.add(pinball)

	# Games
	game1 = Game(
		name="Addams Family",
		status=GameStatus.working,
		x=0,
		y=0,
		location=main_room,
		category=pinball,
		poc_name="Pinball Guy",
		poc_email="poc@example.com",
		poc_phone="123456789"
	)
	game2 = Game(
		name="Street Fighter II",
		status=GameStatus.needs_maintenance,
		x=1,
		y=1,
		location=main_room,
		category=pinball,
		poc_name="Arcade Joe",
		poc_email="arcade@example.com",
		poc_phone="987654321"
	)
	db.add_all([game1, game2])
	db.flush()  # Ensure IDs are generated for foreign keys

	# Logs
	db.add(LogEntry(action="status_change", comments="Initial setup", game=game1, user=alice))
	db.add(LogEntry(action="fault", comments="Button stuck", game=game2, user=bob))

	# Revenue
	db.add(RevenueEntry(amount=15.0, is_token=False, period="Fri-Sun", game=game1, user=alice))
	db.add(RevenueEntry(amount=50.0, is_token=True, period="1 week", game=game2, user=bob))

	db.commit()
	db.close()

if __name__ == "__main__":
	init_demo_data()
