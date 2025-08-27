# app/utils.py
from fastapi.templating import Jinja2Templates
from .models import GameStatus

def status_classes(status: GameStatus) -> str:
	if status == GameStatus.working:
		return "text-green-100 bg-green-700 dark:bg-green-600"
	elif status == GameStatus.needs_maintenance:
		return "text-yellow-100 bg-yellow-700 dark:bg-yellow-600"
	elif status == GameStatus.out_of_order:
		return "text-red-100 bg-red-700 dark:bg-red-600"
	return "text-gray-100 bg-gray-600 dark:bg-gray-500"

def install_template_filters(templates: Jinja2Templates):
	templates.env.globals["status_classes"] = status_classes


