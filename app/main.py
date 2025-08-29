# main.py

# --- Imports ---
import os
import json
import shutil
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, File, Form,
                   HTTPException, Request, UploadFile)
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app import crud, database, models
from app.database import SessionLocal, engine
from app.models import Base
from app.utils import install_template_filters


# --- Pydantic Models ---
class LocationUpdateForm(BaseModel):
    name: str
    rows: int
    columns: int
    cell_size: int
    token_value: float


# --- Database Initialization ---
Base.metadata.create_all(bind=engine)


# --- FastAPI App Setup ---
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="barcade-secret")

# Mount static files and configure Jinja2 templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["has_logo"] = Path("app/static/images/logo.png").exists()
templates.env.globals["datetime"] = datetime
install_template_filters(templates)


# --- Dependencies ---
def get_db():
    """Dependency to get a database session for a request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Helper Functions ---

def get_current_user(request: Request, db: Session):
    """Retrieves the current logged-in user from the session."""
    pin = request.session.get("pin")
    if pin:
        return crud.get_user_by_pin(db, pin)
    return None

def get_selected_location(request: Request):
    """Retrieves the selected location ID from the session."""
    return request.session.get("location_id")

def require_logged_in(request: Request):
    """Raises an HTTPException if the user is not logged in."""
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=401, detail="Unauthorized")

def _send_email(to_addr: str, subject: str, body: str):
    """Sends an email using SMTP settings from environment variables."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USERNAME")
    pwd = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", user or "no-reply@example.com")
    starttls = os.getenv("SMTP_STARTTLS", "true").lower() != "false"

    if not all([host, port, sender]):
        return  # Mail not configured, skip silently

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as s:
        if starttls:
            s.starttls()
        if user and pwd:
            s.login(user, pwd)
        s.send_message(msg)


# =============================================================================
# --- Core UI & Dashboard Routes
# =============================================================================

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Renders the main dashboard page, the entry point of the application."""
    user = get_current_user(request, db)
    location_id = get_selected_location(request)

    # If no users exist, trigger the first-run setup wizard
    if not crud.get_users(db):
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "user": None, "role": None,
            "locations": [], "selected_location": None, "games": [],
            "first_run": True
        })

    locations = crud.get_locations(db)
    # Default to the first location if none is selected
    if not location_id and locations:
        location_id = locations[0].id
        request.session["location_id"] = location_id

    selected_location = crud.get_location_by_id(db, location_id) if location_id else None
    games = crud.get_games_by_location(db, location_id) if location_id else []

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user,
        "role": user.role.value if user else None,
        "locations": locations, "selected_location": selected_location, "games": games
    })

@app.get("/location-selector", response_class=HTMLResponse)
def location_selector(request: Request, db: Session = Depends(get_db)):
    """Renders the location selector dropdown component for the header."""
    locations = crud.get_locations(db)
    selected_location_id = get_selected_location(request)
    return templates.TemplateResponse("location_selector.html", {
        "request": request, "locations": locations, "selected_location_id": selected_location_id
    })

@app.get("/grid-fragment", response_class=HTMLResponse)
def grid_fragment(request: Request, db: Session = Depends(get_db)):
    """Renders just the game grid, used for HTMX partial page updates."""
    user = get_current_user(request, db)
    location_id = request.session.get("location_id")
    selected_location = crud.get_location_by_id(db, location_id) if location_id else None
    games = crud.get_games_by_location(db, location_id) if location_id else []
    return templates.TemplateResponse("grid_fragment.html", {
        "request": request, "user": user,
        "selected_location": selected_location, "games": games
    })


# =============================================================================
# --- Authentication Routes
# =============================================================================

@app.post("/login")
def login(pin: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    """Handles user login with a PIN and sets session data."""
    user = crud.get_user_by_pin(db, pin)
    if user:
        request.session["pin"] = pin
        request.session["user_id"] = str(user.id)
        request.session["name"] = user.name
        request.session["role"] = user.role.value
        request.session["is_manager"] = (user.role == models.UserRole.admin)
        request.session["logged_in"] = True
    return RedirectResponse(url="/", status_code=303)

@app.get("/logout")
def logout(request: Request):
    """Clears the session to log the user out."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

@app.post("/select-location")
def select_location(location_id: int = Form(...), request: Request = None):
    """Sets the user's currently selected location in the session."""
    request.session["location_id"] = location_id
    return RedirectResponse(url="/", status_code=303)


# =============================================================================
# --- First-Run Setup Wizard Routes
# =============================================================================

@app.get("/setup/first-run", response_class=HTMLResponse)
def setup_first_run(request: Request):
    """Renders the initial step of the setup wizard modal."""
    return templates.TemplateResponse("setup/first_run_modal.html", {"request": request})

@app.post("/setup/first-run/admin", response_class=HTMLResponse)
def setup_create_admin(
    request: Request, name: str = Form(...), pin: str = Form(...),
    db: Session = Depends(get_db)
):
    """Step 1: Creates the first admin user and auto-logs them in."""
    if crud.get_users(db):
        return HTMLResponse("Already initialized", status_code=400)

    new_user = crud.create_user(db, name=name, pin=pin, role=models.UserRole.admin)

    # Automatically log in the new admin to continue the wizard
    request.session["pin"] = pin
    request.session["user_id"] = str(new_user.id)
    request.session["name"] = new_user.name
    request.session["role"] = new_user.role.value
    request.session["is_manager"] = True
    request.session["logged_in"] = True

    return templates.TemplateResponse("setup/step_location.html", {"request": request})

@app.post("/setup/first-run/location", response_class=HTMLResponse)
def setup_create_location(
    request: Request, name: str = Form(...), rows: int = Form(...),
    columns: int = Form(...), cell_size: int = Form(...),
    token_value: float = Form(...), db: Session = Depends(get_db)
):
    """Step 2: Creates the first location."""
    crud.create_location(db, name, rows, columns, cell_size, token_value)
    return templates.TemplateResponse("setup/step_category.html", {"request": request})

@app.post("/setup/first-run/category", response_class=HTMLResponse)
def setup_create_category(
    request: Request, name: str = Form(...),
    icon: Optional[str] = Form(None), db: Session = Depends(get_db)
):
    """Step 3: Creates the first game category."""
    crud.create_category(db, name, icon)
    return templates.TemplateResponse("setup/step_game.html", {
        "request": request,
        "categories": crud.get_categories(db),
        "locations": crud.get_locations(db)
    })

@app.post("/setup/first-run/game", response_class=Response)
def setup_create_game(
    request: Request, name: str = Form(...), category_id: int = Form(...),
    location_id: Optional[int] = Form(None), db: Session = Depends(get_db)
):
    """Step 4: Creates the first game and completes setup, triggering a full page reload."""
    loc_id = int(location_id) if location_id is not None and str(location_id).strip() else None
    crud.create_game(
        db=db, name=name, category_id=category_id,
        location_id=loc_id,
        x=1 if loc_id else None,  # Default to (1,1) if location is chosen
        y=1 if loc_id else None
    )

    resp = Response(status_code=204)  # No content
    resp.headers["HX-Redirect"] = "/"  # Tell HTMX to do a full-page redirect
    resp.headers["HX-Trigger"] = json.dumps({"close_modal": True})
    return resp


# =============================================================================
# --- Game Interaction Routes
# =============================================================================

@app.get("/game/{game_id}/modal", response_class=HTMLResponse)
def game_modal(game_id: int, request: Request, db: Session = Depends(get_db)):
    """Renders the content for a game's detail modal."""
    user = get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
    game = crud.get_game_by_id(db, game_id)
    return templates.TemplateResponse("game_modal.html", {
        "request": request, "user": user, "game": game
    })

@app.post("/game/{game_id}/move")
def move_game(
    game_id: int, request: Request,
    x: int = Form(...), y: int = Form(...),
    db: Session = Depends(get_db)
):
    """Moves a game to a new (x, y) coordinate, swapping if the cell is occupied."""
    require_logged_in(request)
    game = crud.get_game_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    selected_location_id = get_selected_location(request)
    if game.location_id != selected_location_id:
        raise HTTPException(status_code=400, detail="Game not in selected location")

    location = crud.get_location_by_id(db, selected_location_id)
    if not location or not (1 <= x <= location.columns and 1 <= y <= location.rows):
        raise HTTPException(status_code=422, detail="Target out of bounds")

    original_x, original_y = game.x, game.y
    if original_x == x and original_y == y:
        return JSONResponse({"unchanged": True})

    occupant = crud.get_game_at(db, location_id=location.id, x=x, y=y)
    if occupant and occupant.id != game.id:
        crud.swap_game_positions(db, game_a=game, game_b=occupant)
        return JSONResponse({
            "moved": {"id": game.id, "x": x, "y": y},
            "swapped": {"id": occupant.id, "x": original_x, "y": original_y}
        })
    else:
        crud.update_game_position(db, game=game, x=x, y=y)
        return JSONResponse({"moved": {"id": game.id, "x": x, "y": y}})

@app.get("/game/{game_id}/status-change-prompt", response_class=HTMLResponse)
def status_change_prompt(game_id: int, status: str, request: Request, db: Session = Depends(get_db)):
    """Renders a modal for adding a note when changing a game's status."""
    user = get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)

    game = crud.get_game_by_id(db, game_id)
    submit_url = f"/game/{game.id}/report-fix" if status == 'working' else f"/game/{game.id}/report-fault"
    status_title = status.replace('_', ' ').title()

    return templates.TemplateResponse("status_change_note_modal.html", {
        "request": request, "game": game, "status": status,
        "status_title": status_title, "submit_url": submit_url
    })

@app.post("/game/{game_id}/report-fault")
def report_fault(
    request: Request, game_id: int,
    status: str = Form(...), note: Optional[str] = Form(""),
    db: Session = Depends(get_db), background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Records a fault for a game and sends email notifications if configured."""
    game = crud.get_game_by_id(db, game_id)
    user = get_current_user(request, db)
    if not (game and user):
        return Response(headers={"HX-Trigger": json.dumps({"close_modal": True})})

    new_status = models.GameStatus(status)
    comment = note or f"{new_status.value.replace('_', ' ').title()} reported"
    crud.report_fault(db, game=game, user_id=user.id, comment=comment, status=new_status)

    # Send email notifications for critical faults
    notify_enabled = os.getenv("EMAIL_NOTIFY_ON_FAULTS", "true").lower() in ("1", "true", "yes")
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    fault_statuses = (models.GameStatus.needs_maintenance, models.GameStatus.out_of_order)

    if notify_enabled and smtp_host and new_status in fault_statuses:
        recipients = db.query(models.User).filter(
            models.User.notify == True, models.User.email.isnot(None), models.User.email != ""
        ).all()
        subject = f"[Barcade App] {game.name} marked {new_status.value.replace('_', ' ')}"
        body_tmpl = (
            "Hi {name},\n\nThe game '{game}' was marked '{status}' by {actor}.\n\n"
            "Note: {comment}\n\n— You received this email because your user is subscribed to game fault updates."
        )
        for u in recipients:
            if u.id != user.id and u.email:
                background_tasks.add_task(
                    _send_email, to_addr=u.email.strip(), subject=subject,
                    body=body_tmpl.format(
                        name=u.name, game=game.name, status=new_status.value.replace('_', ' '),
                        actor=user.name, comment=comment or "-"
                    )
                )

    triggers = {
        "status_changed": {"message": f"'{game.name}' marked {new_status.value.replace('_', ' ')}.",
                           "id": game.id, "status": new_status.value},
        "refresh_grid": True, "close_modal": True
    }
    return Response(status_code=204, headers={"HX-Trigger": json.dumps(triggers)})

@app.post("/game/{game_id}/report-fix")
def report_fix(
    request: Request, game_id: int, note: Optional[str] = Form(""),
    db: Session = Depends(get_db)
):
    """Records that a game has been fixed and is now 'working'."""
    game = crud.get_game_by_id(db, game_id)
    user = get_current_user(request, db)
    if not (game and user):
        return Response(headers={"HX-Trigger": json.dumps({"close_modal": True})})

    comment = note or "Marked as working"
    crud.report_fix(db, game=game, user_id=user.id, comment=comment)

    triggers = {
        "status_changed": {"message": f"'{game.name}' marked working."},
        "refresh_grid": True, "close_modal": True
    }
    return Response(status_code=204, headers={"HX-Trigger": json.dumps(triggers)})

@app.post("/game/{game_id}/log-revenue")
def log_revenue(
    request: Request, game_id: int,
    amount: float = Form(...), is_token: bool = Form(...),
    collected_at: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Logs a revenue collection entry for a game."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    game = crud.get_game_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    try:
        parsed_ts = datetime.fromisoformat(collected_at) if collected_at else None
        if parsed_ts and not parsed_ts.tzinfo:
            parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
    except Exception:
        parsed_ts = None  # Fall back to "now" in CRUD if parsing fails

    crud.log_revenue(
        db=db, game=game, user_id=user.id, amount=amount,
        is_token=is_token, collected_at=parsed_ts
    )

    resp = templates.TemplateResponse("game_modal.html", {
        "request": request, "user": user, "game": crud.get_game_by_id(db, game_id)
    })
    resp.headers["HX-Trigger"] = json.dumps({"revenue_logged": {"message": "Revenue logged"}})
    return resp


# =============================================================================
# --- Data & History API Routes
# =============================================================================

@app.get("/game/{game_id}/status-history", response_class=HTMLResponse)
def status_history(request: Request, game_id: int, db: Session = Depends(get_db)):
    """Renders the status history log for a game in a modal."""
    require_logged_in(request)
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        return HTMLResponse("Game not found.", status_code=404)

    status_entries = db.query(models.LogEntry).filter(
        models.LogEntry.game_id == game_id
    ).order_by(models.LogEntry.timestamp.desc()).all()

    return templates.TemplateResponse("status_history_modal.html", {
        "request": request, "game": game, "entries": status_entries
    })

@app.get("/game/{game_id}/revenue-history", response_class=HTMLResponse)
def revenue_history(request: Request, game_id: int, db: Session = Depends(get_db)):
    """Renders the revenue history log for a game in a modal (admin only)."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        return HTMLResponse("Game not found.", status_code=404)

    revenue_entries = db.query(models.RevenueEntry).filter(
        models.RevenueEntry.game_id == game_id
    ).order_by(models.RevenueEntry.timestamp.desc()).all()

    return templates.TemplateResponse("revenue_history_modal.html", {
        "request": request, "game": game, "entries": revenue_entries
    })

@app.get("/game/{game_id}/status-series")
def status_series_api(
    game_id: int, start: Optional[str] = None, end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """API endpoint to get calculated uptime/downtime series data for charts."""
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    now = datetime.now(timezone.utc)
    end_dt = datetime.fromisoformat(end).astimezone(timezone.utc) if end else now
    start_dt = datetime.fromisoformat(start).astimezone(timezone.utc) if start else (end_dt - timedelta(days=30))
    if start_dt >= end_dt:
        return {"daily": [], "totals": {"uptime_hours": 0.0, "downtime_hours": 0.0}}

    logs = db.query(models.LogEntry).filter(
        models.LogEntry.game_id == game_id
    ).order_by(models.LogEntry.timestamp.asc()).all()

    # Ensure all timestamps are timezone-aware for accurate calculations
    for e in logs:
        if e.timestamp and e.timestamp.tzinfo is None:
            e.timestamp = e.timestamp.replace(tzinfo=timezone.utc)

    # Find status at the beginning of the time window
    prev = next((log for log in reversed(logs) if log.timestamp < start_dt), None)
    current_status = prev.action if prev else "working"

    # Initialize daily buckets for downtime
    days = {}
    day_cursor = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    while day_cursor < end_dt:
        days[day_cursor] = 0.0
        day_cursor += timedelta(days=1)

    # Walk through time segments defined by log entries to calculate downtime
    time_cursor = start_dt
    logs_in_window = [e for e in logs if start_dt <= e.timestamp <= end_dt]

    for entry in logs_in_window:
        seg_start, seg_end = time_cursor, entry.timestamp
        if seg_end > seg_start and current_status == "out_of_order":
            # Distribute this segment's downtime across all affected days
            d = seg_start.replace(hour=0, minute=0, second=0, microsecond=0)
            while d < seg_end:
                day_start = max(seg_start, d)
                day_end = min(seg_end, d + timedelta(days=1))
                days[d] += (day_end - day_start).total_seconds()
                d += timedelta(days=1)
        time_cursor, current_status = seg_end, entry.action

    # Calculate downtime for the final segment (from last log to end of window)
    if time_cursor < end_dt and current_status == "out_of_order":
        d = time_cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        while d < end_dt:
            day_start = max(time_cursor, d)
            day_end = min(end_dt, d + timedelta(days=1))
            if day_start in days:
                days[day_start] += (day_end - day_start).total_seconds()
            d += timedelta(days=1)

    # Format the calculated data for the response
    daily_stats, total_downtime_sec = [], 0
    for day, down_sec in sorted(days.items()):
        total_downtime_sec += down_sec
        window_start = max(day, start_dt)
        window_end = min(day + timedelta(days=1), end_dt)
        day_window_sec = (window_end - window_start).total_seconds()
        up_sec = max(day_window_sec - down_sec, 0.0)
        daily_stats.append({
            "t": day.isoformat(),
            "uptime_hours": round(up_sec / 3600.0, 2),
            "downtime_hours": round(down_sec / 3600.0, 2),
        })

    total_window_sec = (end_dt - start_dt).total_seconds()
    total_uptime_sec = max(total_window_sec - total_downtime_sec, 0.0)

    return {
        "daily": daily_stats,
        "totals": {
            "uptime_hours": round(total_uptime_sec / 3600.0, 2),
            "downtime_hours": round(total_downtime_sec / 3600.0, 2),
        }
    }

@app.get("/game/{game_id}/revenue-series")
def revenue_series_api(
    game_id: int, start: Optional[str] = None, end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """API endpoint to get raw revenue data points for charts."""
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    now = datetime.now(timezone.utc)
    end_dt = datetime.fromisoformat(end).astimezone(timezone.utc) if end else now
    start_dt = datetime.fromisoformat(start).astimezone(timezone.utc) if start else end_dt - timedelta(days=30)

    entries = db.query(models.RevenueEntry).filter(
        models.RevenueEntry.game_id == game_id,
        models.RevenueEntry.timestamp >= start_dt,
        models.RevenueEntry.timestamp <= end_dt
    ).order_by(models.RevenueEntry.timestamp.asc()).all()

    token_value = game.location.token_value if game.location else 1.0
    to_cash = lambda amount, is_token: (amount * token_value) if is_token else amount

    series = []
    for e in entries:
        ts = e.timestamp.replace(tzinfo=timezone.utc) if not e.timestamp.tzinfo else e.timestamp
        series.append({
            "t": ts.isoformat(),
            "amount": round(to_cash(e.amount, e.is_token), 2),
            "raw_amount": e.amount,
            "type": "tokens" if e.is_token else "cash"
        })

    return {"series": series, "start": start_dt.isoformat(), "end": end_dt.isoformat()}


# =============================================================================
# --- Settings Routes (Admin Only)
# =============================================================================

# --- Settings: Main Modal & Tab Rendering ---

@app.get("/settings/modal", response_class=HTMLResponse)
def settings_modal(request: Request, db: Session = Depends(get_db)):
    """Renders the main settings modal which contains various management tabs."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    return templates.TemplateResponse("settings_modal.html", {
        "request": request,
        "locations": db.query(models.Location).all(),
        "selected_location": crud.get_location_by_id(db, get_selected_location(request)),
        "users": db.query(models.User).all(),
        "games": db.query(models.Game).all(),
        "user": user
    })

@app.get("/settings/locations", response_class=HTMLResponse)
def settings_locations(request: Request, db: Session = Depends(get_db)):
    """Renders the 'Locations' tab content for the settings modal."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("settings_locations.html", {
        "request": request, "user": user, "locations": crud.get_locations(db)
    })

@app.get("/settings/games", response_class=HTMLResponse)
def settings_games(request: Request, db: Session = Depends(get_db)):
    """Renders the 'Games' tab content for the settings modal."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("settings_games.html", {
        "request": request, "user": user, "games": crud.get_all_games(db)
    })

@app.get("/settings/categories", response_class=HTMLResponse)
def settings_categories(request: Request, db: Session = Depends(get_db)):
    """Renders the 'Categories' tab content for the settings modal."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("settings_categories.html", {
        "request": request, "user": user, "categories": crud.get_categories(db)
    })

@app.get("/settings/users", response_class=HTMLResponse)
def settings_users(request: Request, db: Session = Depends(get_db)):
    """Renders the 'Users' tab content for the settings modal."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("settings_users.html", {
        "request": request, "users": crud.get_users(db), "user": user
    })

@app.get("/settings/admin", response_class=HTMLResponse)
def settings_admin_tab(request: Request, db: Session = Depends(get_db)):
    """Renders the 'Admin Tools' tab content for the settings modal."""
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("settings_admin.html", {
        "request": request, "user": user,
        "games": db.query(models.Game).order_by(models.Game.name).all()
    })

# --- Settings: Location Management ---

@app.get("/settings/location/add", response_class=HTMLResponse)
def add_location_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("location_add_modal.html", {"request": request})

@app.post("/settings/location/add", response_class=Response)
def save_new_location(
    name: str = Form(...), rows: int = Form(...), columns: int = Form(...),
    cell_size: int = Form(...), token_value: float = Form(...),
    db: Session = Depends(get_db), request: Request = None
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    new_loc = crud.create_location(
        db=db, name=name, rows=rows, columns=columns,
        cell_size=cell_size, token_value=token_value
    )
    trigger = {"location_saved": {"message": f"Location '{new_loc.name}' created"}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.get("/settings/location/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(location_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    location = crud.get_location_by_id(db, location_id)
    if not location:
        return HTMLResponse("Location not found", status_code=404)
    return templates.TemplateResponse("location_edit_modal.html", {
        "request": request, "location": location
    })

@app.post("/settings/location/{location_id}/edit", response_class=Response)
def save_location_edit(
    location_id: int, name: str = Form(...), rows: int = Form(...),
    columns: int = Form(...), cell_size: int = Form(...),
    token_value: float = Form(...), request: Request = None, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    location = crud.get_location_by_id(db, location_id)
    if not location:
        return HTMLResponse("Location not found", status_code=404)

    # **FIX**: Reverted to direct attribute assignment and db.commit()
    location.name = name
    location.rows = rows
    location.columns = columns
    location.cell_size = cell_size
    location.token_value = token_value
    db.commit()
    db.refresh(location)

    trigger = {"location_saved": {"message": f"Location '{location.name}' updated"}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.delete("/settings/location/{location_id}/delete", response_class=HTMLResponse)
def delete_location(location_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    location = crud.get_location_by_id(db, location_id)
    if not location:
        return HTMLResponse("Location not found", status_code=404)

    # Prevent deletion if games are assigned to this location
    if location.games:
        return templates.TemplateResponse("settings_location_delete_error.html", {
            "request": request, "location_name": location.name
        })

    location_name = location.name
    crud.delete_location(db, location)

    response = settings_locations(request, db=db)
    response.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"Location '{location_name}' deleted"},
        "refresh_grid": True
    })
    return response

# --- Settings: Game Management ---

@app.get("/settings/games/add", response_class=HTMLResponse)
def add_game_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("game_add_modal.html", {
        "request": request,
        "categories": db.query(models.Category).order_by(models.Category.name).all(),
        "locations": crud.get_locations(db)
    })

@app.post("/settings/games/add", response_class=Response)
def save_new_game(
    request: Request, name: str = Form(...), category_id: int = Form(...),
    location_id: Optional[int] = Form(None),
    x: Optional[int] = Form(None), y: Optional[int] = Form(None),
    poc_name: Optional[str] = Form(None), poc_email: Optional[str] = Form(None),
    poc_phone: Optional[str] = Form(None), icon_upload: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    icon_filename = None
    if icon_upload and icon_upload.filename:
        save_path = Path("app/static/images") / icon_upload.filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(icon_upload.file, buffer)
        icon_filename = icon_upload.filename

    new_game = crud.create_game(
        db=db, name=name, category_id=category_id, location_id=location_id,
        x=x, y=y, poc_name=poc_name, poc_email=poc_email,
        poc_phone=poc_phone, icon=icon_filename
    )
    trigger = {
        "game_saved": {"message": f"Game '{new_game.name}' has been created."},
        "refresh_grid": True
    }
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.get("/settings/games/{game_id}/edit", response_class=HTMLResponse)
def edit_game_modal(request: Request, game_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    game = db.query(models.Game).filter_by(id=game_id).first()
    if not game:
        return HTMLResponse("Game not found", status_code=404)

    return templates.TemplateResponse("game_edit_modal.html", {
        "request": request, "user": user, "game": game,
        "categories": db.query(models.Category).order_by(models.Category.name).all(),
        "locations": crud.get_locations(db)
    })

@app.post("/settings/games/{game_id}/edit", response_class=Response)
def save_game_changes(
    request: Request, game_id: int, name: str = Form(...),
    category_id: int = Form(...), location_id: Optional[int] = Form(None),
    x: Optional[int] = Form(None), y: Optional[int] = Form(None),
    poc_name: Optional[str] = Form(None), poc_email: Optional[str] = Form(None),
    poc_phone: Optional[str] = Form(None), icon_upload: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    game = db.query(models.Game).filter_by(id=game_id).first()
    if not game:
        return HTMLResponse("Game not found", status_code=404)

    # **FIX**: Reverted to direct attribute assignment and db.commit()
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

    trigger = {
        "game_saved": {"message": f"Game '{game.name}' updated."},
        "refresh_grid": True
    }
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.delete("/settings/games/{game_id}/delete", response_class=HTMLResponse)
def delete_game(game_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    game = crud.get_game_by_id(db, game_id)
    if not game:
        return HTMLResponse("Game not found", status_code=404)

    name = game.name
    # Assuming crud.delete_game handles deleting dependent logs/revenue
    crud.delete_game(db, game)

    resp = settings_games(request, db=db)
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"Game '{name}' deleted."},
        "refresh_grid": True
    })
    return resp

# --- Settings: Category Management ---

@app.get("/settings/category/add", response_class=HTMLResponse)
def add_category_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("category_add_modal.html", {"request": request})

@app.post("/settings/category/add", response_class=Response)
def save_new_category(
    request: Request, name: str = Form(...), icon: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    if crud.get_category_by_name(db, name.strip()):
        return templates.TemplateResponse("category_add_modal.html", {
            "request": request, "error": f"A category named '{name}' already exists.",
            "name": name, "icon": icon or ""
        })

    new_cat = crud.create_category(db, name=name, icon=icon)
    trigger = {"settings_saved": {"message": f"Category '{new_cat.name}' created."}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.get("/settings/category/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(category_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("Category not found", status_code=404)
    return templates.TemplateResponse("category_edit_modal.html", {
        "request": request, "category": category
    })

@app.post("/settings/category/{category_id}/edit", response_class=Response)
def save_category_changes(
    category_id: int, request: Request, name: str = Form(...),
    icon: Optional[str] = Form(None), db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("Category not found", status_code=404)

    existing = crud.get_category_by_name(db, name.strip())
    if existing and existing.id != category.id:
        return templates.TemplateResponse("category_edit_modal.html", {
            "request": request, "category": category,
            "error": f"A category named '{name}' already exists."
        })

    updated = crud.update_category(db, category, name=name, icon=icon)
    trigger = {"settings_saved": {"message": f"Category '{updated.name}' updated."}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.delete("/settings/category/{category_id}/delete", response_class=HTMLResponse)
def delete_category(category_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("Category not found", status_code=404)
    if category.games:
        return HTMLResponse("Cannot delete: category is in use by games.", status_code=400)

    name = category.name
    crud.delete_category(db, category)

    response = settings_categories(request, db=db)
    response.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"Category '{name}' deleted."}
    })
    return response

# --- Settings: User Management ---

@app.get("/settings/user/add", response_class=HTMLResponse)
def add_user_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    return templates.TemplateResponse("user_add_modal.html", {
        "request": request, "roles": models.UserRole
    })

@app.post("/settings/user/add", response_class=Response)
def save_new_user(
    request: Request, name: str = Form(...), pin: str = Form(...),
    role: models.UserRole = Form(...), email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None), notify: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    if crud.get_user_by_pin(db, pin):
        return templates.TemplateResponse("_user_add_form.html", {
            "request": request, "roles": models.UserRole, "name": name,
            "role": role.value, "email": email or "", "phone": phone or "",
            "notify": bool(notify), "error": f"PIN '{pin}' is already taken."
        })

    new_user = crud.create_user(
        db, name=name, pin=pin, role=role, email=email,
        phone=phone, notify=bool(notify)
    )
    trigger = {"user_saved": {"message": f"User '{new_user.name}' has been created."}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.get("/settings/user/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    user_to_edit = crud.get_user_by_id(db, user_id)
    if not user_to_edit:
        return HTMLResponse("User not found", status_code=404)
    return templates.TemplateResponse("user_edit_modal.html", {
        "request": request, "user_to_edit": user_to_edit, "roles": models.UserRole
    })

@app.post("/settings/user/{user_id}/edit", response_class=Response)
def save_user_changes(
    user_id: int, request: Request, name: str = Form(...),
    pin: Optional[str] = Form(None), role: models.UserRole = Form(...),
    email: Optional[str] = Form(None), phone: Optional[str] = Form(None),
    notify: Optional[str] = Form(None), db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    user_to_edit = crud.get_user_by_id(db, user_id)
    if not user_to_edit:
        return HTMLResponse("User not found", status_code=404)

    updated_user = crud.update_user(
        db, user=user_to_edit, name=name, pin=pin, role=role,
        email=email, phone=phone, notify=bool(notify)
    )
    trigger = {"user_saved": {"message": f"User '{updated_user.name}' has been updated."}}
    return Response(headers={"HX-Trigger": json.dumps(trigger)})

@app.delete("/settings/user/{user_id}/delete", response_class=HTMLResponse)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)
    if user.id == user_id:
        return HTMLResponse("You cannot delete your own account.", status_code=400)

    user_to_delete = crud.get_user_by_id(db, user_id)
    if not user_to_delete:
        return HTMLResponse("User not found.", status_code=404)

    user_name = user_to_delete.name
    crud.delete_user(db, user_to_delete)

    response = settings_users(request, db=db)
    response.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"User '{user_name}' has been deleted."}
    })
    return response

# --- Settings: Admin Tools ---

@app.post("/settings/clear-status-history", response_class=HTMLResponse)
def clear_status_history(request: Request, game_id: int = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    db.query(models.LogEntry).filter(models.LogEntry.game_id == game_id).delete()
    db.commit()

    resp = settings_admin_tab(request, db)
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": "Status history cleared for the selected game."}
    })
    return resp

@app.post("/settings/clear-revenue-history", response_class=HTMLResponse)
def clear_revenue_history(request: Request, game_id: int = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("Unauthorized", status_code=403)

    db.query(models.RevenueEntry).filter(models.RevenueEntry.game_id == game_id).delete()
    db.commit()

    resp = settings_admin_tab(request, db)
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": "Revenue history cleared for the selected game."}
    })
    return resp