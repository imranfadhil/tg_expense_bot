from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.base import Base
from bot.db.repo import ExpenseRepo
from bot.keyboards.inline import build_dashboard_button, report_nav_kb
from bot.webapp.app import create_webapp, get_repo
from bot.webapp.auth import (
    create_dashboard_token,
    verify_dashboard_token,
    verify_telegram_init_data,
)


def _make_telegram_init_data(
    user_id: int = 1001,
    first_name: str = "Imran",
    bot_token: str = "123456:TEST_BOT_TOKEN",
    auth_date: int | None = None,
    start_param: str | None = None,
) -> str:
    user_json = json.dumps(
        {"id": user_id, "first_name": first_name, "username": "imran"},
        separators=(",", ":"),
    )
    params: dict[str, str] = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_json,
    }
    if start_param:
        params["start_param"] = start_param

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    sig = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    params["hash"] = sig
    return urlencode(params)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


def test_verify_telegram_init_data():
    bot_token = "999999:SECRET_TEST_TOKEN"
    init_data = _make_telegram_init_data(
        user_id=4242, first_name="Aisha", bot_token=bot_token
    )
    parsed = verify_telegram_init_data(init_data, bot_token=bot_token)
    assert parsed is not None
    assert parsed["user_id"] == 4242
    assert parsed["display_name"] == "Aisha"

    # Wrong bot token should fail
    assert verify_telegram_init_data(init_data, bot_token="wrong:token") is None

    # Expired auth_date should fail
    old_init_data = _make_telegram_init_data(
        user_id=4242,
        bot_token=bot_token,
        auth_date=int(time.time()) - 90000,
    )
    assert (
        verify_telegram_init_data(
            old_init_data, bot_token=bot_token, max_age_seconds=3600
        )
        is None
    )


def test_dashboard_signed_tokens():
    secret = "test-signing-secret"
    token = create_dashboard_token(
        household_id=7,
        member_id=3,
        telegram_user_id=1001,
        secret=secret,
        ttl_seconds=300,
    )
    payload = verify_dashboard_token(token, secret=secret)
    assert payload is not None
    assert payload["hh"] == 7
    assert payload["mem"] == 3
    assert payload["uid"] == 1001

    # Tampered signature fails
    assert verify_dashboard_token(token + "0", secret=secret) is None

    # Expired token fails
    expired = create_dashboard_token(
        household_id=7, secret=secret, ttl_seconds=-10
    )
    assert verify_dashboard_token(expired, secret=secret) is None


def test_dashboard_keyboard_buttons():
    https_url = "https://demo.trycloudflare.com/?token=abc"
    btn_priv = build_dashboard_button(https_url, is_private_chat=True)
    assert btn_priv is not None
    assert btn_priv.web_app is not None
    assert btn_priv.web_app.url == https_url
    assert btn_priv.url is None

    # Group chats do not allow web_app buttons in inline keyboards; should fall back to url
    btn_grp = build_dashboard_button(https_url, is_private_chat=False)
    assert btn_grp is not None
    assert btn_grp.web_app is None
    assert btn_grp.url == https_url

    kb = report_nav_kb(2026, 10, dashboard_url=https_url, is_private_chat=True)
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[1][0].web_app.url == https_url


@pytest.mark.asyncio
async def test_webapp_api_full_flow(db_session, monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123456:TEST_BOT_TOKEN")
    monkeypatch.setattr(settings, "bot_passphrase", None)

    repo = ExpenseRepo(db_session)
    app = create_webapp()

    async def override_get_repo():
        yield repo

    app.dependency_overrides[get_repo] = override_get_repo

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        # 1. Health & HTML index
        r_health = await client.get("/api/health")
        assert r_health.status_code == 200
        assert r_health.json()["status"] == "ok"

        r_html = await client.get("/")
        assert r_html.status_code == 200
        assert "Family Expense Live Dashboard" in r_html.text

        # 2. Authenticate with Telegram initData
        init_data = _make_telegram_init_data(
            user_id=777,
            first_name="Imran",
            bot_token="123456:TEST_BOT_TOKEN",
        )
        r_login = await client.post("/api/auth/login", json={"init_data": init_data})
        assert r_login.status_code == 200
        login_data = r_login.json()
        token = login_data["token"]
        assert login_data["household"]["invite_code"].startswith("FAM-")

        headers = {"Authorization": f"Bearer {token}"}

        # 3. Create expenses (quick_text and structured)
        r_exp1 = await client.post(
            "/api/expenses",
            headers=headers,
            json={"quick_text": "45.50 groceries Giant supermarket"},
        )
        assert r_exp1.status_code == 200
        exp1 = r_exp1.json()["expense"]
        assert exp1["amount"] == 45.50
        assert exp1["category_name"] == "groceries"
        assert exp1["effective_once_off"] is False

        r_exp2 = await client.post(
            "/api/expenses",
            headers=headers,
            json={"quick_text": "850 auto Maybank #recurring"},
        )
        assert r_exp2.status_code == 200
        exp2 = r_exp2.json()["expense"]
        assert exp2["amount"] == 850.0
        assert exp2["is_recurring"] is True
        assert exp2["effective_once_off"] is True

        # 4. Update household & category budgets
        r_bud = await client.put(
            "/api/budgets",
            headers=headers,
            json={
                "currency": "RM",
                "household_budget": 3500,
                "categories": [
                    {
                        "category_id": exp1["category_id"],
                        "monthly_budget": 600,
                    }
                ],
            },
        )
        assert r_bud.status_code == 200
        assert r_bud.json()["household_budget"] == 3500.0
        assert r_bud.json()["currency"] == "RM"

        # 5. Fetch live dashboard payload
        r_dash = await client.get("/api/dashboard", headers=headers)
        assert r_dash.status_code == 200
        dash = r_dash.json()
        assert dash["household"]["currency"] == "RM"
        assert dash["report"]["total_spent"] == 895.50
        assert dash["report"]["fixed_spent"] == 850.00
        assert dash["report"]["variable_spent"] == 45.50
        assert dash["report"]["tx_count"] == 2
        assert dash["recurring"]["count"] == 1
        assert dash["recurring"]["total_monthly"] == 850.00
        assert len(dash["daily_series"]) >= 28

        # 6. Edit an expense
        r_patch = await client.patch(
            f"/api/expenses/{exp1['id']}",
            headers=headers,
            json={"amount": 52.00, "description": "Giant hypermarket"},
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["expense"]["amount"] == 52.00
        assert r_patch.json()["expense"]["description"] == "Giant hypermarket"

        # 7. Create a custom category
        r_cat = await client.post(
            "/api/categories",
            headers=headers,
            json={
                "name": "pets",
                "display_name": "🐶 Pets",
                "monthly_budget": 200,
                "is_fixed": False,
            },
        )
        assert r_cat.status_code == 200
        assert r_cat.json()["category"]["name"] == "pets"
        assert r_cat.json()["category"]["budget"] == 200.0

        # 8. Delete expense
        r_del = await client.delete(f"/api/expenses/{exp1['id']}", headers=headers)
        assert r_del.status_code == 200
        assert r_del.json()["deleted"] is True


@pytest.mark.asyncio
async def test_webapp_passphrase_unlock_flow(db_session, monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123456:TEST_BOT_TOKEN")
    monkeypatch.setattr(settings, "bot_passphrase", "family-pin-999")

    repo = ExpenseRepo(db_session)
    app = create_webapp()

    async def override_get_repo():
        yield repo

    app.dependency_overrides[get_repo] = override_get_repo

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        init_data = _make_telegram_init_data(
            user_id=888,
            first_name="Sara",
            bot_token="123456:TEST_BOT_TOKEN",
        )
        # Without passphrase, should return 403 PASSPHRASE_REQUIRED
        r_locked = await client.post(
            "/api/auth/login", json={"init_data": init_data}
        )
        assert r_locked.status_code == 403
        assert r_locked.json()["detail"]["code"] == "PASSPHRASE_REQUIRED"

        # With correct passphrase, unlocks and returns token
        r_unlock = await client.post(
            "/api/auth/login",
            json={"init_data": init_data, "passphrase": "family-pin-999"},
        )
        assert r_unlock.status_code == 200
        token = r_unlock.json()["token"]

        # Subsequent requests with initData header now succeed directly
        r_dash = await client.get(
            "/api/dashboard", headers={"X-Telegram-Init-Data": init_data}
        )
        assert r_dash.status_code == 200
        assert r_dash.json()["current_member"]["display_name"] == "Sara"
        assert token

