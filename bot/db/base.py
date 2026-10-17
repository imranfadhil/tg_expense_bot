from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.sqlalchemy_database_url,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    from sqlalchemy import inspect, select, text
    from bot.db.models import DEFAULT_CATEGORIES, FIXED_CATEGORY_NAMES, Category, Household

    def _migrate_columns(sync_conn) -> None:
        inspector = inspect(sync_conn)
        table_names = set(inspector.get_table_names())
        if "categories" in table_names:
            cat_cols = {c["name"] for c in inspector.get_columns("categories")}
            if "is_fixed" not in cat_cols:
                sync_conn.execute(
                    text("ALTER TABLE categories ADD COLUMN is_fixed BOOLEAN NOT NULL DEFAULT FALSE")
                )
        if "expenses" in table_names:
            exp_cols = {c["name"] for c in inspector.get_columns("expenses")}
            if "is_once_off" not in exp_cols:
                sync_conn.execute(
                    text("ALTER TABLE expenses ADD COLUMN is_once_off BOOLEAN NOT NULL DEFAULT FALSE")
                )
            if "is_recurring" not in exp_cols:
                sync_conn.execute(
                    text("ALTER TABLE expenses ADD COLUMN is_recurring BOOLEAN NOT NULL DEFAULT FALSE")
                )
            if "source_chat_id" not in exp_cols:
                sync_conn.execute(
                    text("ALTER TABLE expenses ADD COLUMN source_chat_id BIGINT NULL")
                )
            if "source_message_id" not in exp_cols:
                sync_conn.execute(
                    text("ALTER TABLE expenses ADD COLUMN source_message_id BIGINT NULL")
                )
            if "bot_message_id" not in exp_cols:
                sync_conn.execute(
                    text("ALTER TABLE expenses ADD COLUMN bot_message_id BIGINT NULL")
                )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_columns)

    # Backfill new default categories and fixed flags for existing households
    legacy_renames = {
        "car_loan": "auto",
        "school_fees": "school",
        "salary_deductions": "saving",
    }
    async with AsyncSessionLocal() as session:
        households = (await session.execute(select(Household))).scalars().all()
        if households:
            existing_cats = (await session.execute(select(Category))).scalars().all()
            cats_by_hh: dict[int, dict[str, Category]] = {}
            for c in existing_cats:
                cats_by_hh.setdefault(c.household_id, {})[c.name.lower()] = c

            changed = False
            for hh in households:
                hh_cats = cats_by_hh.get(hh.id, {})
                for old_key, new_key in legacy_renames.items():
                    if old_key in hh_cats and new_key not in hh_cats:
                        cat_obj = hh_cats.pop(old_key)
                        cat_obj.name = new_key
                        cat_obj.is_fixed = True
                        hh_cats[new_key] = cat_obj
                        changed = True
                for key, label in DEFAULT_CATEGORIES:
                    should_be_fixed = key in FIXED_CATEGORY_NAMES
                    if key not in hh_cats:
                        session.add(
                            Category(
                                household_id=hh.id,
                                name=key,
                                display_name=label,
                                is_fixed=should_be_fixed,
                            )
                        )
                        changed = True
                    else:
                        if should_be_fixed and not hh_cats[key].is_fixed:
                            hh_cats[key].is_fixed = True
                            changed = True
                        if key == "saving" and hh_cats[key].display_name == "💼 Upfront Salary Deductions":
                            hh_cats[key].display_name = label
                            changed = True
            if changed:
                await session.commit()



