# app/seed.py
from app.database import SessionLocal
from app import models

def run():
    db = SessionLocal()

    location = db.query(models.Location).filter_by(name="Main Arcade").first()
    if not location:
        location = models.Location(
            name="Main Arcade",
            rows=5,
            columns=5,
            cell_size=80,
            token_value=1.0,
            background_image=None
        )
        db.add(location)
    else:
        location.rows = 5
        location.columns = 5
        location.cell_size = 80
        location.token_value = 1.0
    db.flush()


    # --- Categories ---
    arcade_cat = db.query(models.Category).filter_by(name="Arcade").first()
    if not arcade_cat:
        arcade_cat = models.Category(name="Arcade")
        db.add(arcade_cat)
        db.flush()

    pinball_cat = db.query(models.Category).filter_by(name="Pinball").first()
    if not pinball_cat:
        pinball_cat = models.Category(name="Pinball")
        db.add(pinball_cat)
        db.flush()

    # --- Games ---
    sf2 = db.query(models.Game).filter_by(name="Street Fighter II").first()
    if not sf2:
        sf2 = models.Game(
            name="Street Fighter II",
            category_id=arcade_cat.id,
            location_id=location.id,
            x=0,
            y=0,
            status=models.GameStatus.working
        )
        db.add(sf2)

    indiana = db.query(models.Game).filter_by(name="Indiana Jones").first()
    if not indiana:
        indiana = models.Game(
            name="Indiana Jones",
            category_id=pinball_cat.id,
            location_id=location.id,
            x=1,
            y=0,
            status=models.GameStatus.working
        )
        db.add(indiana)

    # --- Users ---
    boss = db.query(models.User).filter_by(name="Boss").first()
    if not boss:
        boss = models.User(
            name="Boss",
            pin="1111",
            role=models.UserRole.admin,
            email="boss@example.com"
        )
        db.add(boss)
    else:
        boss.pin = "1111"
        boss.role = models.UserRole.admin
        boss.email = "boss@example.com"

    employee = db.query(models.User).filter_by(name="Employee").first()
    if not employee:
        employee = models.User(
            name="Employee",
            pin="2222",
            role=models.UserRole.user,
            email="employee@example.com"
        )
        db.add(employee)
    else:
        employee.pin = "2222"
        employee.role = models.UserRole.user
        employee.email = "employee@example.com"

    db.commit()
    db.close()
    print("✅ Seed complete: ensured settings, location, categories, games, and users are correct")

if __name__ == "__main__":
    run()
