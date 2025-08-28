# app/main.py
from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
import json
from typing import Optional
from pydantic import BaseModel
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone

from app import database, models, crud
from app.models import Game, LogEntry, RevenueEntry, UserRole, Base
from app.database import engine, SessionLocal
from app.utils import install_template_filters


class LocationUpdateForm(BaseModel):
	name: str
	rows: int
	columns: int
	cell_size: int
	token_value: float

Base.metadata.create_all(bind=engine)


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="barcade-secret")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

from pathlib import Path
templates.env.globals["has_logo"] = Path("app/static/images/logo.png").exists()


install_template_filters(templates)


# Dependency
def get_db():
	db = SessionLocal()
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

def _to_cash(amount: float, is_token: bool, token_value: float) -> float:
    return (amount * token_value) if is_token else amount

def _daterange_floor(dt: datetime, granularity: str) -> datetime:
    if granularity == "daily":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=dt.tzinfo)
    if granularity == "weekly":
        # ISO week starts Monday
        d0 = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=dt.tzinfo)
        return d0 - timedelta(days=d0.weekday())
    if granularity == "monthly":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=dt.tzinfo)
    return dt

def _step(granularity: str) -> timedelta:
    return timedelta(days=1 if granularity == "daily" else (7 if granularity == "weekly" else 30))

def build_revenue_series(entries, start: datetime, end: datetime, granularity: str, token_value: float):
    """
    entries: ordered ASC by timestamp (Log-style: each entry's amount belongs to the interval since previous entry)
    We distribute each entry's cash amount uniformly across [prev_time, curr_time).
    Missing periods naturally get 0.
    """
    # Guard
    if start >= end:
        return []

    # Ensure tz-aware
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:   end = end.replace(tzinfo=timezone.utc)

    # Seed bins
    bins = {}
    cur = _daterange_floor(start, granularity)
    step = _step(granularity)
    while cur < end:
        bins[cur] = 0.0
        cur = cur + (timedelta(days=1) if granularity == "daily" else
                     timedelta(days=7) if granularity == "weekly" else
                     # monthly: approximate 30-day buckets (simple; avoids heavy calendar math)
                     timedelta(days=30))

    # Prepare an artificial first previous time = start window (so earliest entry in range distributes from its own previous or the window start)
    prev_time = None
    prev_amount_cash = None

    # We’ll compute on each entry's timestamp boundary
    # First, prepend a synthetic "begin marker" at start so that first real entry can consider earlier time if needed
    # Actually we need the previous entry outside the range. So fetch the previous one if exists:
    # (We’ll query it in the route; here assume route gives us prev_outside if needed.)
    # This helper expects entries to include one synthetic lead-in tuple: (prev_timestamp, None) to set prev_time.
    # To keep function simple for you, we allow entries[0] carry prev via attribute ._prev; route sets it.

    # Walk entries
    for idx, e in enumerate(entries):
        curr_time = e.timestamp
        if curr_time.tzinfo is None:
            curr_time = curr_time.replace(tzinfo=timezone.utc)

        if prev_time is None:
            # use synthetic previous from attribute or clamp to start
            prev_time = getattr(e, "_prev_ts", start)
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)

        # clamp interval to [start, end)
        interval_start = max(prev_time, start)
        interval_end = min(curr_time, end)
        if interval_end > interval_start:
            duration = (interval_end - interval_start).total_seconds()
            amount_cash = _to_cash(e.amount, e.is_token, token_value)
            # Spread uniformly across the interval into overlapping bins
            # Iterate per-day/week/month bin boundaries
            bin_cursor = _daterange_floor(interval_start, granularity)
            step_td = (timedelta(days=1) if granularity == "daily"
                       else timedelta(days=7) if granularity == "weekly"
                       else timedelta(days=30))
            total_interval_seconds = (curr_time - prev_time).total_seconds()
            # Avoid div by zero (same timestamp) – treat as instant: put all into the bin of curr_time
            denom = max(total_interval_seconds, 1.0)
            while bin_cursor < interval_end:
                next_boundary = bin_cursor + step_td
                seg_start = max(interval_start, bin_cursor)
                seg_end = min(interval_end, next_boundary)
                if seg_end > seg_start:
                    frac = (seg_end - seg_start).total_seconds() / denom
                    bins_key = _daterange_floor(bin_cursor, granularity)
                    if bins_key in bins:
                        bins[bins_key] += amount_cash * frac
                bin_cursor = next_boundary

        prev_time = curr_time

    # Build sorted list
    out = []
    for k in sorted(bins.keys()):
        out.append({"t": k.isoformat(), "amount": round(bins[k], 2)})
    return out


def require_logged_in(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Routes ---

@app.get("/location-selector", response_class=HTMLResponse)
def location_selector(request: Request, db: Session = Depends(get_db)):
    locations = crud.get_locations(db)
    selected_location_id = get_selected_location(request)
    return templates.TemplateResponse("location_selector.html", {
        "request": request,
        "locations": locations,
        "selected_location_id": selected_location_id
    })

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    location_id = get_selected_location(request)

    if not crud.get_users(db):  # no users in DB
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user": None,
            "role": None,
            "locations": [],
            "selected_location": None,
            "games": [],
            "first_run": True  # flag for wizard launcher
        })

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

@app.post("/game/{game_id}/move")
def move_game(
    game_id: int,
    request: Request,
    x: int = Form(...),
    y: int = Form(...),
    db: Session = Depends(get_db)
):
    # Require a logged in user (any role)
    require_logged_in(request)

    # Validate game and get current location context
    game = crud.get_game_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    selected_location_id = get_selected_location(request)
    if game.location_id != selected_location_id:
        raise HTTPException(status_code=400, detail="Game not in selected location")

    # Validate coordinates within grid bounds
    location = crud.get_location_by_id(db, selected_location_id)
    if not location:
        raise HTTPException(status_code=400, detail="No location selected")

    if x < 1 or y < 1 or x > location.columns or y > location.rows:
        raise HTTPException(status_code=422, detail="Target out of bounds")

	# Move or swap
    occupant = crud.get_game_at(db, location_id=location.id, x=x, y=y)
    original_x, original_y = game.x, game.y

	# If target cell is the same, do nothing
    if original_x == x and original_y == y:
        return JSONResponse({"unchanged": True})

    if occupant and occupant.id != game.id:
        crud.swap_game_positions(db, game_a=game, game_b=occupant)
        return JSONResponse({
			"moved": {"id": game.id, "x": x, "y": y},
			"swapped": {"id": occupant.id, "x": original_x, "y": original_y}
		})

	# Otherwise just move
    crud.update_game_position(db, game=game, x=x, y=y)
    return JSONResponse({
		"moved": {"id": game.id, "x": x, "y": y}
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
		return HTMLResponse("Unauthorized", status_code=401)


	return templates.TemplateResponse("game_modal.html", {
		"request": request,
		"user": user,
		"game": game
	})

@app.get("/game/{game_id}/status-change-prompt", response_class=HTMLResponse)
def status_change_prompt(game_id: int, status: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return HTMLResponse("Unauthorized", status_code=401)
        
    game = crud.get_game_by_id(db, game_id)
    
    if status == 'working':
        submit_url = f"/game/{game.id}/report-fix"
    else:
        submit_url = f"/game/{game.id}/report-fault"
    
    status_title = status.replace('_', ' ').title()

    return templates.TemplateResponse("status_change_note_modal.html", {
        "request": request,
        "game": game,
        "status": status,
        "status_title": status_title,
        "submit_url": submit_url
    })


@app.post("/game/{game_id}/report-fault")
def report_fault(
	request: Request,
	game_id: int,
	status: str = Form(...),
	note: Optional[str] = Form(""),
	db: Session = Depends(get_db)
):
	game = crud.get_game_by_id(db, game_id)
	user = get_current_user(request, db)

	if game and user:
		comment = note or f"{status.replace('_', ' ').title()} reported"
		crud.report_fault(
			db,
			game=game,
			user_id=user.id,
			comment=comment,
			status=models.GameStatus(status)
		)

	return RedirectResponse(url="/", status_code=303)


@app.post("/game/{game_id}/report-fix")
def report_fix(
	request: Request,
	game_id: int,
	note: Optional[str] = Form(""),
	db: Session = Depends(get_db)
):
	game = crud.get_game_by_id(db, game_id)
	user = get_current_user(request, db)

	if game and user:
		comment = note or "Marked as working"
		crud.report_fix(
			db,
			game=game,
			user_id=user.id,
			comment=comment
		)

	return RedirectResponse(url="/", status_code=303)

@app.post("/game/{game_id}/log-revenue")
def log_revenue(
    request: Request,
    game_id: int,
    amount: float = Form(...),
    is_token: bool = Form(...),
    collected_at: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # Require a logged-in user
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    game = crud.get_game_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Parse optional date/datetime
    parsed_ts = None
    if collected_at:
        try:
            # Accept 'YYYY-MM-DD' or full ISO 'YYYY-MM-DDTHH:MM'
            if len(collected_at) == 10:
                dt = datetime.fromisoformat(collected_at)
            else:
                dt = datetime.fromisoformat(collected_at)
            parsed_ts = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            parsed_ts = None  # ignore invalid input and fall back to "now"

    # Persist
    crud.log_revenue(
        db=db,
        game=game,
        user_id=user.id,
        amount=amount,
        is_token=is_token,
        collected_at=parsed_ts
    )

    # Re-fetch fresh game (to reflect any changes if you later add derived fields)
    game = crud.get_game_by_id(db, game_id)

    resp = templates.TemplateResponse("game_modal.html", {
        "request": request,
        "user": user,
        "game": crud.get_game_by_id(db, game_id),
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "revenue_logged": {"message": "Revenue logged"}
    })
    return resp



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

def _floor_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

@app.get("/game/{game_id}/status-series")
def status_series_api(
    game_id: int,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    now = datetime.now(timezone.utc)
    end_dt = datetime.fromisoformat(end).astimezone(timezone.utc) if end else now
    start_dt = datetime.fromisoformat(start).astimezone(timezone.utc) if start else (end_dt - timedelta(days=30))
    if start_dt >= end_dt:
        return {"daily": [], "totals": {"uptime_hours": 0.0, "downtime_hours": 0.0}}

    # Get all logs plus the last one before start to know starting status
    q = (db.query(LogEntry)
           .filter(LogEntry.game_id == game_id)
           .order_by(LogEntry.timestamp.asc()))
    logs = q.all()

    # Find previous status before window
    prev = None
    for e in logs:
        if e.timestamp and e.timestamp.tzinfo is None:
            e.timestamp = e.timestamp.replace(tzinfo=timezone.utc)
    for e in logs:
        if e.timestamp < start_dt:
            prev = e
        else:
            break

    # Determine status at start
    current_status = prev.action if prev else "working"  # default to working if no history

    # Seed per-day buckets
    day = _floor_day(start_dt)
    days = {}
    while day < end_dt:
        days[day] = 0.0  # downtime seconds per day
        day += timedelta(days=1)

    # Walk segments across the window
    cursor = start_dt
    for e in [x for x in logs if start_dt <= x.timestamp <= end_dt]:
        seg_start = cursor
        seg_end = e.timestamp
        if seg_end > seg_start and current_status == "out_of_order":
            # Accumulate downtime into day buckets
            d = _floor_day(seg_start)
            while d < seg_end:
                day_start = max(seg_start, d)
                day_end = min(seg_end, d + timedelta(days=1))
                days[_floor_day(d)] += (day_end - day_start).total_seconds()
                d += timedelta(days=1)
        # Move status forward
        cursor = seg_end
        current_status = e.action

    # Tail segment to end_dt
    if cursor < end_dt and current_status == "out_of_order":
        d = _floor_day(cursor)
        while d < end_dt:
            day_start = max(cursor, d)
            day_end = min(end_dt, d + timedelta(days=1))
            days[_floor_day(d)] += (day_end - day_start).total_seconds()
            d += timedelta(days=1)

    # Build outputs
    daily = []
    for k in sorted(days.keys()):
        day_start = k
        day_end = min(k + timedelta(days=1), end_dt)

        # Window overlap for this day (handles first/last partial days)
        window_start = max(day_start, start_dt)
        window_end = min(day_end, end_dt)
        window_seconds = max((window_end - window_start).total_seconds(), 0.0)

        down_sec = days[k]
        up_sec = max(window_seconds - down_sec, 0.0)

        daily.append({
            "t": k.isoformat(),
            "uptime_hours": round(up_sec / 3600.0, 2),
            "downtime_hours": round(down_sec / 3600.0, 2),
        })

    total_seconds = (end_dt - start_dt).total_seconds()
    downtime_seconds = sum(v for v in days.values())
    uptime_seconds = max(total_seconds - downtime_seconds, 0.0)

    return {
        "daily": daily,
        "totals": {
            "uptime_hours": round(uptime_seconds / 3600.0, 2),
            "downtime_hours": round(downtime_seconds / 3600.0, 2),
        }
    }


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

from datetime import datetime, timedelta, timezone

@app.get("/game/{game_id}/revenue-series")
def revenue_series_api(
    game_id: int,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db)
):
    game = db.query(models.Game).filter(models.Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Time window defaults: last 30 days
    now = datetime.now(timezone.utc)
    end_dt = datetime.fromisoformat(end).astimezone(timezone.utc) if end else now
    start_dt = datetime.fromisoformat(start).astimezone(timezone.utc) if start else end_dt - timedelta(days=30)

    entries = (
        db.query(RevenueEntry)
          .filter(RevenueEntry.game_id == game_id,
                  RevenueEntry.timestamp >= start_dt,
                  RevenueEntry.timestamp <= end_dt)
          .order_by(RevenueEntry.timestamp.asc())
          .all()
    )

    token_value = game.location.token_value if game.location else 1.0

    def to_cash(amount: float, is_token: bool) -> float:
        return (amount * token_value) if is_token else amount

    series = []
    for e in entries:
        ts = e.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        series.append({
            "t": ts.isoformat(),
            "amount": round(to_cash(e.amount, e.is_token), 2),
            "raw_amount": e.amount,
            "type": "tokens" if e.is_token else "cash"
        })

    return {
        "series": series,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat()
    }

@app.get("/settings/modal", response_class=HTMLResponse)
def settings_modal(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	locations = db.query(models.Location).all()
	selected_location = crud.get_location_by_id(db, get_selected_location(request))
	users = db.query(models.User).all()
	games = db.query(models.Game).all()

	return templates.TemplateResponse("settings_modal.html", {
		"request": request,
		"locations": locations,
		"selected_location": selected_location,
		"users": users,
		"games": games,
        "user": user
	})

@app.get("/settings/locations", response_class=HTMLResponse)
def settings_locations(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	locations = crud.get_locations(db)

	return templates.TemplateResponse("settings_locations.html", {
		"request": request,
		"user": user,
		"locations": locations
	})

@app.get("/settings/categories", response_class=HTMLResponse)
def settings_categories(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    categories = crud.get_categories(db)
    return templates.TemplateResponse("settings_categories.html", {
        "request": request,
        "user": user,
        "categories": categories
    })

@app.get("/settings/category/add", response_class=HTMLResponse)
def add_category_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    return templates.TemplateResponse("category_add_modal.html", {
        "request": request
    })

@app.post("/settings/category/add", response_class=HTMLResponse)
def save_new_category(
    request: Request,
    name: str = Form(...),
    icon: str | None = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    # Uniqueness check (name is unique in model)
    existing = crud.get_category_by_name(db, name.strip())
    if existing:
        return templates.TemplateResponse("category_add_modal.html", {
            "request": request,
            "error": f"A category named '{name}' already exists.",
            "name": name,
            "icon": icon or ""
        }, status_code=200)

    new_cat = crud.create_category(db, name=name, icon=icon)

    trigger = {
        "settings_saved": {"message": f"Category '{new_cat.name}' created."}
    }
    return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger)})

@app.get("/settings/category/{category_id}/edit", response_class=HTMLResponse)
def edit_category_form(category_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("<p>Category not found</p>", status_code=404)

    return templates.TemplateResponse("category_edit_modal.html", {
        "request": request,
        "category": category
    })

@app.post("/settings/category/{category_id}/edit", response_class=HTMLResponse)
def save_category_changes(
    category_id: int,
    request: Request,
    name: str = Form(...),
    icon: str | None = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("<p>Category not found</p>", status_code=404)

    # Prevent duplicate name (other record)
    existing = crud.get_category_by_name(db, name.strip())
    if existing and existing.id != category.id:
        return templates.TemplateResponse("category_edit_modal.html", {
            "request": request,
            "category": category,
            "error": f"A category named '{name}' already exists."
        }, status_code=200)

    updated = crud.update_category(db, category, name=name, icon=icon)

    trigger = {
        "settings_saved": {"message": f"Category '{updated.name}' updated."}
    }
    return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger)})

@app.delete("/settings/category/{category_id}/delete", response_class=HTMLResponse)
def delete_category(category_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    category = crud.get_category_by_id(db, category_id)
    if not category:
        return HTMLResponse("<p>Category not found</p>", status_code=404)

    name = category.name
    if category.games:
        return HTMLResponse("<p>Cannot delete: category in use by games.</p>", status_code=400)

    crud.delete_category(db, category)

    # Return refreshed tab (like users/location delete flows)
    response = settings_categories(request, db=db)
    response.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"Category '{name}' deleted."}
    })
    return response

@app.get("/settings/admin", response_class=HTMLResponse)
def settings_admin_tab(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    games = db.query(models.Game).order_by(models.Game.name).all()
    return templates.TemplateResponse("settings_admin.html", {
        "request": request,
        "user": user,
        "games": games,
    })



@app.post("/settings/clear-status-history", response_class=HTMLResponse)
def clear_status_history(request: Request, game_id: int = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    # Clear only the selected game's status logs
    db.query(models.LogEntry).filter(models.LogEntry.game_id == game_id).delete(synchronize_session=False)
    db.commit()

    games = db.query(models.Game).order_by(models.Game.name).all()
    resp = templates.TemplateResponse("settings_admin.html", {
        "request": request,
        "user": user,
        "games": games,
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": "Status history cleared for the selected game."}
    })
    return resp

@app.post("/settings/clear-revenue-history", response_class=HTMLResponse)
def clear_revenue_history(request: Request, game_id: int = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    # Clear only the selected game's revenue
    db.query(models.RevenueEntry).filter(models.RevenueEntry.game_id == game_id).delete(synchronize_session=False)
    db.commit()

    games = db.query(models.Game).order_by(models.Game.name).all()
    resp = templates.TemplateResponse("settings_admin.html", {
        "request": request,
        "user": user,
        "games": games,
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": "Revenue history cleared for the selected game."}
    })
    return resp


@app.get("/settings/location/add", response_class=HTMLResponse)
def add_location_form(request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	return templates.TemplateResponse("location_add_modal.html", {"request": request})


@app.post("/settings/location/add", response_class=HTMLResponse)
def save_new_location(
	request: Request, name: str = Form(...), rows: int = Form(...),
	columns: int = Form(...), cell_size: int = Form(...),
	token_value: float = Form(...), db: Session = Depends(get_db)
):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	new_location = crud.create_location(
		db=db, name=name, rows=rows, columns=columns,
		cell_size=cell_size, token_value=token_value
	)
	trigger_data = {"location_saved": {"message": f"Location '{new_location.name}' created"}}
	return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger_data)})


@app.get("/settings/location/{location_id}/edit", response_class=HTMLResponse)
def edit_location_form(location_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	location = crud.get_location_by_id(db, location_id)
	if not location:
		return HTMLResponse("<p>Location not found</p>", status_code=404)

	return templates.TemplateResponse("location_edit_modal.html", {
		"request": request,
		"location": location
	})

@app.post("/settings/location/{location_id}/edit", response_class=HTMLResponse)
def save_location_edit(
	request: Request, location_id: int, name: str = Form(...),
	rows: int = Form(...), columns: int = Form(...),
	cell_size: int = Form(...), token_value: float = Form(...),
	db: Session = Depends(get_db)
):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	location_to_update = crud.get_location_by_id(db, location_id)
	if not location_to_update:
		return HTMLResponse("<p>Location not found</p>", status_code=404)

	location_to_update.name = name
	location_to_update.rows = rows
	location_to_update.columns = columns
	location_to_update.cell_size = cell_size
	location_to_update.token_value = token_value
	db.commit()
	db.refresh(location_to_update)

	trigger_data = {"location_saved": {"message": f"Location '{location_to_update.name}' updated"}}
	return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger_data)})


@app.delete("/settings/location/{location_id}/delete", response_class=HTMLResponse)
def delete_location(location_id: int, request: Request, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	location_to_delete = crud.get_location_by_id(db, location_id)
	if not location_to_delete:
		return HTMLResponse("<p>Location not found</p>", status_code=404)
	
	if location_to_delete.games:
		# Change the status code on this response to 200
		return templates.TemplateResponse("settings_location_delete_error.html", {
			"request": request,
			"location_name": location_to_delete.name
		}, status_code=200) # <-- MODIFIED

	location_name = location_to_delete.name
	crud.delete_location(db, location_to_delete)
	
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
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	games = crud.get_all_games(db)

	return templates.TemplateResponse("settings_games.html", {
		"request": request,
		"user": user,
		"games": games
	})
 
@app.delete("/settings/games/{game_id}/delete", response_class=HTMLResponse)
def delete_game(game_id: int, request: Request, db: Session = Depends(get_db)):
    # AuthZ: only admins
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    game = crud.get_game_by_id(db, game_id)
    if not game:
        return HTMLResponse("<p>Game not found</p>", status_code=404)

    name = game.name

    # Remove dependent rows first to avoid FK issues
    db.query(models.LogEntry).filter(models.LogEntry.game_id == game_id).delete(synchronize_session=False)
    db.query(models.RevenueEntry).filter(models.RevenueEntry.game_id == game_id).delete(synchronize_session=False)
    db.commit()

    # Delete the game
    db.delete(game)
    db.commit()

    # Return refreshed Games tab and toast
    resp = settings_games(request, db=db)  # reuses your existing tab renderer
    resp.headers["HX-Trigger"] = json.dumps({
        "settings_saved": {"message": f"Game '{name}' deleted."},
        "refresh_grid": True
    })
    return resp


@app.get("/settings/users", response_class=HTMLResponse)
def settings_users(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    users = crud.get_users(db)

    return templates.TemplateResponse("settings_users.html", {
        "request": request,
        "users": users,
        "user": user
    })

@app.get("/settings/user/add", response_class=HTMLResponse)
def add_user_form(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    return templates.TemplateResponse("user_add_modal.html", {
        "request": request,
        "roles": models.UserRole
    })

@app.post("/settings/user/add", response_class=HTMLResponse)
def save_new_user(
    request: Request,
    name: str = Form(...),
    pin: str = Form(...),
    role: models.UserRole = Form(...),
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    existing_user = crud.get_user_by_pin(db, pin)
    if existing_user:
        return templates.TemplateResponse("_user_add_form.html", {
            "request": request,
            "roles": models.UserRole,
            "name": name,
            "role": role.value,
            "error": f"PIN '{pin}' is already taken by another user."
        }, status_code=200)

    new_user = crud.create_user(db, name=name, pin=pin, role=role)

    trigger_data = {
		"user_saved": {"message": f"User '{new_user.name}' has been created."}
	}
    return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger_data)})

@app.delete("/settings/user/{user_id}/delete", response_class=HTMLResponse)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    if current_user.id == user_id:
        return HTMLResponse("<p>You cannot delete your own account.</p>", status_code=400)

    user_to_delete = crud.get_user_by_id(db, user_id)
    if not user_to_delete:
        return HTMLResponse("<p>User not found.</p>", status_code=404)

    user_name = user_to_delete.name
    crud.delete_user(db, user_to_delete)

    response = settings_users(request, db=db)
    response.headers["HX-Trigger"] = json.dumps({
		"settings_saved": {"message": f"User '{user_name}' has been deleted."}
	})
    return response

@app.get("/settings/user/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)
    
    user_to_edit = crud.get_user_by_id(db, user_id)
    if not user_to_edit:
        return HTMLResponse("<p>User not found</p>", status_code=404)

    return templates.TemplateResponse("user_edit_modal.html", {
        "request": request,
        "user_to_edit": user_to_edit,
        "roles": models.UserRole
    })

@app.post("/settings/user/{user_id}/edit", response_class=HTMLResponse)
def save_user_changes(
    user_id: int,
    request: Request,
    name: str = Form(...),
    pin: Optional[str] = Form(None),
    role: models.UserRole = Form(...),
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    user_to_edit = crud.get_user_by_id(db, user_id)
    if not user_to_edit:
        return HTMLResponse("<p>User not found</p>", status_code=404)

    updated_user = crud.update_user(db, user=user_to_edit, name=name, pin=pin, role=role)

    trigger_data = {
		"user_saved": {"message": f"User '{updated_user.name}' has been updated."}
	}
    return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger_data)})

@app.get("/settings/games/add", response_class=HTMLResponse)
def add_game_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

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
    # AuthZ
    user = get_current_user(request, db)
    if not user or user.role != models.UserRole.admin:
        return HTMLResponse("<p>Unauthorized</p>", status_code=403)

    # Normalize optional fields (treat empty strings as None)
    def _clean(v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    location_id = _clean(location_id)
    x = int(x) if _clean(x) is not None else None
    y = int(y) if _clean(y) is not None else None
    poc_name = _clean(poc_name)
    poc_email = _clean(poc_email)
    poc_phone = _clean(poc_phone)

    # Optional: validate x/y within grid bounds if provided together with a valid location
    if location_id is not None and x is not None and y is not None:
        loc = crud.get_location_by_id(db, int(location_id))
        if not loc:
            return templates.TemplateResponse("game_add_modal.html", {
                "request": request,
                "categories": db.query(models.Category).order_by(models.Category.name).all(),
                "locations": crud.get_locations(db),
                "error": "Selected location not found."
            }, status_code=200)
        if x < 1 or y < 1 or x > loc.columns or y > loc.rows:
            return templates.TemplateResponse("game_add_modal.html", {
                "request": request,
                "categories": db.query(models.Category).order_by(models.Category.name).all(),
                "locations": crud.get_locations(db),
                "error": f"Coordinates ({x},{y}) are out of bounds for '{loc.name}'."
            }, status_code=200)

    # Handle optional icon upload
    icon_filename = None
    if icon_upload and icon_upload.filename:
        save_path = Path("app/static/images") / icon_upload.filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(icon_upload.file, buffer)
        icon_filename = icon_upload.filename

    # Create the game (status defaults to working in CRUD)
    new_game = crud.create_game(
        db=db,
        name=name,
        category_id=int(category_id),
        location_id=int(location_id) if location_id is not None else None,
        x=x,
        y=y,
        poc_name=poc_name,
        poc_email=poc_email,
        poc_phone=poc_phone,
        icon=icon_filename
    )

    # Notify frontend via HX-Trigger (settings modal will reopen on Games tab per your listeners)
    trigger_data = {
        "game_saved": {
            "message": f"Game '{new_game.name}' has been created.",
            "id": new_game.id,
            "name": new_game.name,
            "icon": new_game.icon,
            "x": new_game.x,
            "y": new_game.y
        }
    }
    return Response(content="", status_code=200, headers={"HX-Trigger": json.dumps(trigger_data)})

@app.get("/settings/games/{game_id}/edit", response_class=HTMLResponse)
def edit_game_modal(request: Request, game_id: int, db: Session = Depends(get_db)):
	user = get_current_user(request, db)
	if not user or user.role != models.UserRole.admin:
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	game = db.query(models.Game).filter_by(id=game_id).first()
	if not game:
		return HTMLResponse("<p>Game not found</p>", status_code=404)
	
	categories = db.query(models.Category).order_by(models.Category.name).all()
	locations = crud.get_locations(db)

	return templates.TemplateResponse("game_edit_modal.html", {
		"request": request,
		"user": user,
		"game": game,
		"categories": categories,
		"locations": locations
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
		return HTMLResponse("<p>Unauthorized</p>", status_code=403)

	game = db.query(models.Game).filter_by(id=game_id).first()
	if not game:
		return HTMLResponse("<p>Game not found</p>", status_code=404)

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
		"game_saved": {
			"id": game.id,
			"name": game.name,
			"icon": game.icon,
			"x": game.x,
			"y": game.y
		}
	}
	return Response(content="", status_code=200,
				headers={"HX-Trigger": json.dumps(trigger_data)})
 
 # --- First-run Setup Wizard ---

@app.get("/setup/first-run", response_class=HTMLResponse)
def setup_first_run(request: Request):
    return templates.TemplateResponse("setup/first_run_modal.html", {
        "request": request
    })

@app.post("/setup/first-run/admin", response_class=HTMLResponse)
def setup_create_admin(
    request: Request,
    name: str = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db)
):
    if crud.get_users(db):  # prevent rerun
        return HTMLResponse("Already initialized", status_code=400)

    new_user = crud.create_user(db, name=name, pin=pin, role=models.UserRole.admin)

    # auto-login the admin
    request.session["pin"] = pin
    request.session["user_id"] = str(new_user.id)
    request.session["name"] = new_user.name
    request.session["role"] = new_user.role.value
    request.session["is_manager"] = True
    request.session["logged_in"] = True

    # move to next step (location)
    return templates.TemplateResponse("setup/step_location.html", {"request": request})

@app.post("/setup/first-run/location", response_class=HTMLResponse)
def setup_create_location(
    request: Request,
    name: str = Form(...),
    rows: int = Form(...),
    columns: int = Form(...),
    cell_size: int = Form(...),
    token_value: float = Form(...),
    db: Session = Depends(get_db)
):
    crud.create_location(db, name, rows, columns, cell_size, token_value)
    return templates.TemplateResponse("setup/step_category.html", {"request": request})

@app.post("/setup/first-run/category", response_class=HTMLResponse)
def setup_create_category(
    request: Request,
    name: str = Form(...),
    icon: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    crud.create_category(db, name, icon)
    return templates.TemplateResponse("setup/step_game.html", {
        "request": request,
        "categories": crud.get_categories(db),
        "locations": crud.get_locations(db)
    })

@app.post("/setup/first-run/game", response_class=HTMLResponse)
def setup_create_game(
    request: Request,
    name: str = Form(...),
    category_id: int = Form(...),
    location_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    # Normalize location_id
    loc_id: Optional[int] = None
    if location_id is not None and str(location_id).strip() != "":
        try:
            loc_id = int(location_id)
        except ValueError:
            loc_id = None

    # If a location is selected, default new game to (1,1)
    default_x = 1 if loc_id is not None else None
    default_y = 1 if loc_id is not None else None

    crud.create_game(
        db=db,
        name=name,
        category_id=int(category_id),
        location_id=loc_id,
        x=default_x,
        y=default_y,
        poc_name=None,
        poc_email=None,
        poc_phone=None,
        icon=None
    )

    # Tell HTMX to perform a FULL PAGE redirect (do not swap HTML into modal)
    resp = Response(content="", status_code=204)  # no body
    resp.headers["HX-Redirect"] = "/"
    # (Optional) also trigger modal close just in case
    resp.headers["HX-Trigger"] = json.dumps({"close_modal": True})
    return resp

