# 🏦 Prudent Family Expense Bot (`aiogram` + PostgreSQL)

A Telegram bot built with **aiogram 3.x**, **SQLAlchemy 2.0 (asyncpg)**, and **PostgreSQL (Docker Compose)** designed for families to log expenses effortlessly and manage household finances prudently from month to month.

---

## ✨ Key Features

1. **👨‍👩‍👧‍👦 Shared Family Household Ledger**
   - Every household gets a unique invite code (`FAM-XXXXXX`).
   - Family members can join the same ledger in **private chat** (`/join FAM-XXXXXX`) OR add the bot to a **Family Telegram Group** and link the group (`/join FAM-XXXXXX`).
   - Automatically tracks *who* logged each expense and breaks down monthly spending by both category and family member.

2. **⚡ Quick Natural Text Logging + 1-Tap Category Correction**
   - Just send a message in chat—no tedious multi-step forms required:
     - `45.50 groceries Giant supermarket`
     - `$12.80 lunch Maxwell hawker`
     - `85 utilities SP electricity bill`
     - `coffee at yakun 6.40`
   - Built-in smart keyword aliases automatically map words like `ntuc`, `fairprice`, `giant` → **🛒 Groceries**, `lunch`, `dinner`, `coffee`, `hawker` → **🍽️ Dining**, `mrt`, `bus`, `grab`, `petrol` → **🚌 Transport**, etc.
   - Every logged expense displays inline **✏️ Category** and **🗑️ Undo** buttons (and if a note doesn't match a known category, it immediately shows 1-tap category buttons).

3. **📊 Month-to-Month Financial & Pace Management**
   - **`/report [YYYY-MM]`**:
     - Total monthly spend, daily average, and **Projected Month-End Spend** based on current run rate.
     - **Prudent Safe Daily Limit** for the remaining days of the month when an overall budget is set.
     - Visual progress bars per category (`████░░░░░░`) and family member breakdown.
     - Interactive inline buttons (`◀️ Prev Month`, `📈 Vs Prev Month`, `Next Month ▶️`).
   - **`/compare [YYYY-MM]`**:
     - **Month-over-Month (MoM)** comparison against the previous month.
     - **Day-N Pace Check**: When viewing the current in-progress month, compares your spending so far against the exact same pro-rated day of last month so you know immediately whether your pace is faster or more prudent.
     - Category-by-category delta (`+$ / +%` or `-$ / -%`).
   - **`/budget`**:
     - Set overall household monthly budget (`/budget total 3000`) and per-category monthly caps (`/budget groceries 600`).
     - Automatic real-time alerts when logging an expense if a category or total budget crosses **80% (⚠️ Warning)** or **100% (🚨 Exceeded)**.
   - **`/export [YYYY-MM]`**:
     - Exports any month's ledger to a clean `.csv` file directly in Telegram for Excel or Google Sheets.

---

## 🚀 Quick Start (Docker Compose)

1. **Create your `.env` file:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set your `BOT_TOKEN` from [@BotFather](https://t.me/BotFather) (and optionally `DEFAULT_CURRENCY` and `TIMEZONE`).
   > 💡 **Tip for Group Chats**: If you plan to use the bot inside a Telegram group chat and want it to read plain text expenses without `/add`, open [@BotFather](https://t.me/BotFather) → `/mybots` → Select your bot → **Bot Settings** → **Group Privacy** → **Turn off**.

2. **Start PostgreSQL + Bot with Docker Compose:**
   ```bash
   docker compose up -d --build
   ```

3. **Check logs:**
   ```bash
   docker compose logs -f bot
   ```

---

## 💻 Local Development & Testing

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Run the test suite
pytest -v
```

