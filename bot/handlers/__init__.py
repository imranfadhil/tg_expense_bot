from aiogram import Router

from bot.handlers.budgets import router as budgets_router
from bot.handlers.expenses import router as expenses_router
from bot.handlers.household import router as household_router
from bot.handlers.reports import router as reports_router


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(household_router)
    root.include_router(budgets_router)
    root.include_router(reports_router)
    root.include_router(expenses_router)
    return root

