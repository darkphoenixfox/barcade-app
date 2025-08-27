# app/main.py
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from . import database, models, crud
from app.utils import install_template_filters
from fastapi import Form, HTTPException
from app.models import Game, LogEntry, RevenueEntry
from app.database import engine
from app.models import Base

# Create all tables at startup if they don't exist
Base.metadata.create_all(bind=engine)


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="barcade-secret")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

from app.utils import install_template_filters
install_template_filters(templates)


# Dependency
def get_db():
	db = database.SessionLocal()
	try:
		yield db
	finally:
		db.close()

# --- Session Helpers ---

def get_current_user(request: Request, db: Session):
	pin = request.session.get("pin")
	if pin:
		return crud.get_user_by_pin(db, pin)
	return None

def get_selected_location(request: Request):
	return request.session.get("location_id", None)

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    location_id = get_selected_location(request)

    locations = crud.get_locations(db)
    if not location_id and locations:
        location_id = locations[0].id
        request.session["location_id"] = location_id

    selected_location = crud.get_location_by_id(db, location_id) if location_id else None
    games = crud.get_games_by_location(db, location_id) if location_id else []

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "role": user.role.value if user else None,
        "locations": locations,
        "selected_location": selected_location,
        "games": games
    })


@app.post("/login")
def login(pin: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
	user = crud.get_user_by_pin(db, pin)
	response = RedirectResponse(url="/", status_code=303)

	if user:
		request.session["pin"] = pin
		request.session["user_id"] = str(user.id)
		request.session["name"] = user.name
		request.session["role"] = user.role.value
		request.session["is_manager"] = user.role == models.UserRole.admin
		request.session["logged_in"] = True
	return response



@app.get("/logout")
def logout(request: Request):
	request.session.clear()
	return RedirectResponse(url="/", status_code=303)

@app.post("/select-location")
def select_location(location_id: int = Form(...), request: Request = None):
	request.session["location_id"] = location_id
	return RedirectResponse(url="/", status_code=303)


@app.get("/game/{game_id}/modal", response_class=HTMLResponse)
def game_modal(game_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	game = crud.get_game_by_id(db, game_id)

	if not user:
		return HTMLResponse("""
			<div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
				<div class="bg-white rounded-lg shadow-lg max-w-md w-full p-6 relative text-center">
					<button class="absolute top-2 right-3 text-gray-500 hover:text-black text-xl"
							onclick="document.getElementById('modal-container').innerHTML = ''">&times;</button>
					<h2 class="text-xl font-bold text-gray-800 mb-2">Please Log In</h2>
					<p class="text-sm text-gray-600">You must be logged in to view game details and report status or revenue.</p>
				</div>
			</div>
		""", status_code=200)


	return templates.TemplateResponse("game_modal.html", {
		"request": request,
		"user": user,
		"game": game
	})

@app.post("/game/{game_id}/report-fault")
def report_fault(
	request: Request,
	game_id: int,
	status: str = Form(...),
	db: Session = Depends(get_db)
):
	game = crud.get_game_by_id(db, game_id)
	user = get_current_user(request, db)

	if game and user:
		crud.report_fault(
			db,
			game=game,
			user_id=user.id,
			comment=f"{status.replace('_', ' ').title()} reported via modal",
			status=models.GameStatus(status)
		)

	return RedirectResponse(url="/", status_code=303)




@app.post("/game/{game_id}/report-fix")
def report_fix(
	request: Request,
	game_id: int,
	db: Session = Depends(get_db)
):
	game = crud.get_game_by_id(db, game_id)
	user = get_current_user(request, db)

	if game and user:
		crud.report_fix(
			db,
			game=game,
			user_id=user.id,
			comment="Marked as working via modal"
		)

	return RedirectResponse(url="/", status_code=303)



@app.post("/game/{game_id}/log-revenue")
def log_revenue(
	request: Request,
	game_id: int,
	amount: float = Form(...),
	is_token: bool = Form(...),
	period: str = Form(""),
	db: Session = Depends(get_db)
):
	game = crud.get_game_by_id(db, game_id)
	user = get_current_user(request, db)

	if game and user:
		crud.log_revenue(
			db,
			game=game,
			user_id=user.id,
			amount=amount,
			is_token=is_token,
			period=period
		)

	return RedirectResponse(url="/", status_code=303)


@app.get("/game/{game_id}/history", response_class=HTMLResponse)
def game_history(game_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	game = crud.get_game_by_id(db, game_id)

	if not user:
		return HTMLResponse("""
		<div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
			<div class="bg-white dark:bg-gray-900 text-black dark:text-white rounded-lg shadow-lg max-w-md w-full p-6 relative text-center">
				<button class="absolute top-2 right-3 text-gray-500 hover:text-white text-xl"
				        onclick="document.getElementById('modal-container').innerHTML = ''">&times;</button>
				<h2 class="text-xl font-bold mb-2">Log in to view history</h2>
			</div>
		</div>
		""", status_code=200)

	logs = game.logs
	revenue = game.revenue_entries

	# combine + sort by timestamp
	all_entries = sorted(
		logs + revenue,
		key=lambda e: e.timestamp,
		reverse=True
	)

	return templates.TemplateResponse("game_history_modal.html", {
		"request": request,
		"user": user,
		"game": game,
		"entries": all_entries
	})

@app.get("/game/{game_id}/status-history", response_class=HTMLResponse)
def status_history(request: Request, game_id: int, db: Session = Depends(get_db)):
	if not request.session.get("logged_in"):
		raise HTTPException(status_code=403)


	game = db.query(Game).filter(Game.id == game_id).first()
	if not game:
		return HTMLResponse("<div class='p-4'>Game not found.</div>", status_code=404)

	status_entries = (
		db.query(LogEntry)
		.filter(LogEntry.game_id == game_id)
		.order_by(LogEntry.timestamp.desc())
		.all()
	)

	return templates.TemplateResponse("status_history_modal.html", {
		"request": request,
		"game": game,
		"entries": status_entries,
	})

@app.get("/game/{game_id}/revenue-history", response_class=HTMLResponse)
def revenue_history(request: Request, game_id: int, db: Session = Depends(get_db)):
	if not request.session.get("is_manager"):
		raise HTTPException(status_code=403)


	game = db.query(Game).filter(Game.id == game_id).first()
	if not game:
		return HTMLResponse("<div class='p-4'>Game not found.</div>", status_code=404)

	revenue_entries = (
		db.query(RevenueEntry)
		.filter(RevenueEntry.game_id == game_id)
		.order_by(RevenueEntry.timestamp.desc())
		.all()
	)

	return templates.TemplateResponse("revenue_history_modal.html", {
		"request": request,
		"game": game,
		"entries": revenue_entries,
	})

@app.get("/settings/modal", response_class=HTMLResponse)
def settings_modal(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

    settings = db.query(models.Settings).first()
    locations = db.query(models.Location).all()
    users = db.query(models.User).all()
    games = db.query(models.Game).all()

    return templates.TemplateResponse("settings_modal.html", {
        "request": request,
        "settings": settings,
        "locations": locations,
        "users": users,
        "games": games
    })

@app.get("/settings/location/{location_id}", response_class=HTMLResponse)
def settings_location(request: Request, location_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

    location = db.query(models.Location).get(location_id)
    return templates.TemplateResponse("settings_location.html", {
        "request": request,
        "location": location
    })


@app.post("/settings/location/{location_id}/save")
def settings_location_save(
    request: Request,
    location_id: int,
    rows: int = Form(...),
    columns: int = Form(...),
    cell_size: int = Form(...),
    token_value: float = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

    location = db.query(models.Location).get(location_id)
    if location:
        location.rows = rows
        location.columns = columns
        location.cell_size = cell_size
        location.token_value = token_value
        db.commit()

    return RedirectResponse("/settings/modal", status_code=303)
