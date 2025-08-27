# app/main.py
from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from . import database, models, crud
from app.utils import install_template_filters
from app.models import Game, LogEntry, RevenueEntry
from app.database import engine
from app.models import Base
import json
from typing import Optional
from pydantic import BaseModel
import shutil
from pathlib import Path

# Pydantic schema for location form data
class LocationUpdateForm(BaseModel):
	name: str
	rows: int
	columns: int
	cell_size: int
	token_value: float

# Create all tables at startup if they don't exist
Base.metadata.create_all(bind=engine)


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="barcade-secret")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

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

	locations = db.query(models.Location).all()
	location_id = request.session.get("location_id")
	selected_location = db.query(models.Location).get(location_id) if location_id else None
	users = db.query(models.User).all()
	games = db.query(models.Game).all()

	return templates.TemplateResponse("settings_modal.html", {
		"request": request,
		"locations": locations,
		"selected_location": selected_location,
		"users": users,
		"games": games
	})

@app.get("/settings/locations", response_class=HTMLResponse)
def settings_locations(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	locations = crud.get_locations(db)

	return templates.TemplateResponse("settings_locations.html", {
		"request": request,
		"user": user,
		"locations": locations
	})


@app.get("/settings/location/add", response_class=HTMLResponse)
def add_location_form(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	return templates.TemplateResponse("location_add_modal.html", {"request": request})


@app.post("/settings/location/add", response_class=HTMLResponse)
def save_new_location(
	request: Request,
	name: str = Form(...),
	rows: int = Form(...),
	columns: int = Form(...),
	cell_size: int = Form(...),
	token_value: float = Form(...),
	db: Session = Depends(get_db)
):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	# Create the new location in the database
	new_location = crud.create_location(
		db=db,
		name=name,
		rows=rows,
		columns=columns,
		cell_size=cell_size,
		token_value=token_value
	)

	# Use the same reload-and-reopen-modal logic as the edit function
	trigger_data = {
		"location_saved": {"message": f"Location '{new_location.name}' created"}
	}
	return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger_data)})


@app.get("/settings/location/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(location_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	location = crud.get_location_by_id(db, location_id)
	if not location:
		return HTMLResponse("<div class='p-4'>Location not found.</div>", status_code=404)

	return templates.TemplateResponse("location_edit_modal.html", {
		"request": request,
		"location": location
	})

@app.post("/settings/location/{location_id}/edit", response_class=HTMLResponse)
def save_location_edit(
	request: Request,
	location_id: int,
	name: str = Form(...),
	rows: int = Form(...),
	columns: int = Form(...),
	cell_size: int = Form(...),
	token_value: float = Form(...),
	db: Session = Depends(get_db)
):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	location_to_update = crud.get_location_by_id(db, location_id)
	if not location_to_update:
		return HTMLResponse("<div class='p-4'>Location not found.</div>", status_code=404)

	# Update location data
	location_to_update.name = name
	location_to_update.rows = rows
	location_to_update.columns = columns
	location_to_update.cell_size = cell_size
	location_to_update.token_value = token_value
	db.commit()
	db.refresh(location_to_update)

	# Prepare a new trigger event for the JS to handle
	trigger_data = {
		"location_saved": {"message": f"Location '{location_to_update.name}' updated"}
	}
	
	# Return an empty response with the trigger header
	# The JS will now handle the reload and subsequent actions
	return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger_data)})


@app.delete("/settings/location/{location_id}/delete", response_class=HTMLResponse)
def delete_location(location_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	location_to_delete = crud.get_location_by_id(db, location_id)
	if not location_to_delete:
		return HTMLResponse("<div class='p-4'>Location not found.</div>", status_code=404)
	
	# SAFETY CHECK: Ensure location has no games before deleting
	if location_to_delete.games:
		return templates.TemplateResponse("settings_location_delete_error.html", {
			"request": request,
			"location_name": location_to_delete.name
		})

	location_name = location_to_delete.name
	crud.delete_location(db, location_to_delete)
	
	# After deleting, reload the locations list and show a toast
	response = settings_locations(request, db=db)
	response.headers["HX-Trigger"] = json.dumps({
		"settings_saved": {"message": f"Location '{location_name}' deleted"},
        "refresh_grid": True
	})
	return response


@app.get("/settings/games", response_class=HTMLResponse)
def settings_games(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	games = crud.get_all_games(db)

	return templates.TemplateResponse("settings_games.html", {
		"request": request,
		"user": user,
		"games": games
	})

@app.get("/settings/games/add", response_class=HTMLResponse)
def add_game_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

    # Fetch data for dropdowns
    categories = db.query(models.Category).order_by(models.Category.name).all()
    locations = crud.get_locations(db)

    return templates.TemplateResponse("game_add_modal.html", {
        "request": request,
        "categories": categories,
        "locations": locations
    })

@app.post("/settings/games/add", response_class=HTMLResponse)
def save_new_game(
    request: Request,
    name: str = Form(...),
    category_id: int = Form(...),
    location_id: Optional[int] = Form(None),
    x: Optional[int] = Form(None),
    y: Optional[int] = Form(None),
    poc_name: Optional[str] = Form(None),
    poc_email: Optional[str] = Form(None),
    poc_phone: Optional[str] = Form(None),
    icon_upload: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

    icon_filename = None
    if icon_upload and icon_upload.filename:
        # Save the uploaded file
        save_path = Path("app/static/images") / icon_upload.filename
        save_path.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(icon_upload.file, buffer)
        icon_filename = icon_upload.filename

    new_game = crud.create_game(
        db=db,
        name=name,
        category_id=category_id,
        location_id=location_id,
        x=x,
        y=y,
        poc_name=poc_name,
        poc_email=poc_email,
        poc_phone=poc_phone,
        icon=icon_filename
    )

    trigger_data = {
        "game_saved": {"message": f"Game '{new_game.name}' created"}
    }
    return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger_data)})

@app.get("/settings/games/{game_id}/edit", response_class=HTMLResponse)
def edit_game_modal(request: Request, game_id: int, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	game = db.query(models.Game).filter_by(id=game_id).first()
	if not game:
		return HTMLResponse("<div class='p-4'>Game not found.</div>", status_code=404)
	
	# --- MODIFIED --- Fetch categories and locations for the dropdowns
	categories = db.query(models.Category).order_by(models.Category.name).all()
	locations = crud.get_locations(db)

	return templates.TemplateResponse("game_edit_modal.html", {
		"request": request,
		"user": user,
		"game": game,
		"categories": categories,
		"locations": locations  # Pass locations to the template
	})

@app.get("/location-selector", response_class=HTMLResponse)
def location_selector(request: Request, db: Session = Depends(get_db)):
    locations = crud.get_locations(db)
    selected_location_id = get_selected_location(request)
    return templates.TemplateResponse("location_selector.html", {
        "request": request,
        "locations": locations,
        "selected_location_id": selected_location_id
    })

@app.post("/settings/games/{game_id}/edit", response_class=HTMLResponse)
def save_game_changes(
	request: Request,
	game_id: int,
	name: str = Form(...),
	category_id: int = Form(...),
	location_id: Optional[int] = Form(None),
	x: Optional[int] = Form(None),
	y: Optional[int] = Form(None),
	poc_name: Optional[str] = Form(None),
	poc_email: Optional[str] = Form(None),
	poc_phone: Optional[str] = Form(None),
    icon_upload: UploadFile = File(None),
	db: Session = Depends(get_db)
):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<div class='p-4'>Unauthorized</div>", status_code=403)

	game = db.query(models.Game).filter_by(id=game_id).first()
	if not game:
		return HTMLResponse("<div class='p-4'>Game not found.</div>", status_code=404)

	# Handle file upload
	if icon_upload and icon_upload.filename:
		save_path = Path("app/static/images") / icon_upload.filename
		save_path.parent.mkdir(parents=True, exist_ok=True)
		with save_path.open("wb") as buffer:
			shutil.copyfileobj(icon_upload.file, buffer)
		game.icon = icon_upload.filename
	
	game.name = name
	game.category_id = category_id
	game.location_id = location_id
	game.x = x
	game.y = y
	game.poc_name = poc_name or None
	game.poc_email = poc_email or None
	game.poc_phone = poc_phone or None
	db.commit()

	trigger_data = {
		"game_saved": {"message": f"Game '{game.name}' updated"}
	}
	return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger_data)})
