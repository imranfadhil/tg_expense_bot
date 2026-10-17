from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot.db.models import Household, Member
from bot.db.repo import ExpenseRepo

router = Router(name="household")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
    member: Member,
) -> None:
    # Support deep-link invite: /start FAM-XXXXXX
    if command.args and command.args.strip().upper().startswith("FAM-"):
        code = command.args.strip().upper()
        is_group = message.chat.type in ("group", "supergroup")
        joined = await repo.join_household_by_code(
            telegram_user_id=member.telegram_user_id,
            display_name=member.display_name,
            invite_code=code,
            chat_id=message.chat.id,
            is_group=is_group,
        )
        if joined:
            household = joined

    members = await repo.get_household_members(household.id)
    member_names = ", ".join(html.escape(m.display_name) for m in members)
    cur = html.escape(household.currency)

    text = (
        f"👋 <b>Welcome to Prudent Family Expense Bot!</b>\n\n"
        f"🏠 <b>Household:</b> {html.escape(household.name)}\n"
        f"👨‍👩‍👧‍👦 <b>Members ({len(members)}):</b> {member_names}\n"
        f"🔑 <b>Family Invite Code:</b> <code>{household.invite_code}</code>\n\n"
        f"⚡ <b>Quick Expense Logging (just send a message!):</b>\n"
        f"• <code>45.50 groceries Giant supermarket</code>\n"
        f"• <code>12.80 lunch Maxwell hawker</code>\n"
        f"• <code>850 auto Maybank</code> <i>(📌 auto once-off)</i>\n"
        f"• <code>450 school</code> <i>(📌 auto once-off)</i>\n"
        f"• <code>1200 shopping new fridge #once</code> <i>(📌 manual once-off)</i>\n\n"
        f"📊 <b>Month-to-Month Financial Management:</b>\n"
        f"• /report — Monthly breakdown, smart pace forecast & safe daily spend\n"
        f"• /compare — Month-over-Month comparison & pace check\n"
        f"• /budget — Set monthly total or category budgets (e.g. <code>/budget groceries 600</code>)\n"
        f"• /recent — View, edit & undo recent expenses\n"
        f"• /categories — View or add custom categories\n"
        f"• /export — Export monthly ledger to CSV\n"
        f"• /family — Share invite code with family members or group chat\n\n"
        f"💡 <i>Tip: Have your partner or family group chat run</i> "
        f"<code>/join {household.invite_code}</code> <i>to share this ledger! ({cur})</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(
    message: Message,
    household: Household,
) -> None:
    cur = html.escape(household.currency)
    text = (
        f"📖 <b>How to Manage Your Family Finances Prudently</b>\n\n"
        f"<b>1. Log & Edit Expenses Instantly</b>\n"
        f"Just type the amount followed by category or keyword and a note:\n"
        f"• <code>38.90 groceries NTUC FairPrice</code>\n"
        f"• <code>14.50 dinner Saizeriya</code>\n"
        f"• <code>850 auto</code> | <code>450 school</code> | <code>600 saving</code>\n"
        f"• Add <code>#once</code> (or tap <b>📌 Once-Off</b> on the reply card) for any one-time purchase so it isn't multiplied in your daily pace!\n"
        f"• <b>Made a typo?</b> Edit your Telegram message directly, or tap <b>💰 Amount</b> / <b>📝 Note</b> / <b>📂 Category</b> on the card (or via /recent)!\n"
        f"<i>(In group chats, you can also type plain messages or use <code>/add 38.90 groceries NTUC</code>)</i>\n\n"
        f"<b>2. Set Prudent Monthly Budgets</b>\n"
        f"• <code>/budget total 3000</code> — Set overall monthly budget to {cur}3,000\n"
        f"• <code>/budget groceries 600</code> — Set Groceries cap to {cur}600\n"
        f"• <code>/budget</code> — View all budget caps & remaining balances\n"
        f"<i>You'll automatically get ⚠️ 80% and 🚨 100% alerts when logging expenses!</i>\n\n"
        f"<b>3. Track Month-to-Month Progress</b>\n"
        f"• /report — Current month summary, fixed vs variable daily avg & projected month-end total\n"
        f"• <code>/report 2026-09</code> — View a specific month\n"
        f"• /compare — Compare this month vs last month (with mid-month pace check!)\n"
        f"• /recent — Last 10 expenses with 1-tap edit & delete\n"
        f"• /export — Download CSV for Excel / Google Sheets\n\n"
        f"<b>4. Family & Settings</b>\n"
        f"• /family — View household members & invite code\n"
        f"• <code>/join FAM-XXXXXX</code> — Join a family ledger\n"
        f"• <code>/currency S$</code> — Change currency symbol\n"
        f"• <code>/addcategory pets 🐶 Pets</code> — Add a custom category (append <code>#fixed</code> for monthly bills)"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("family", "invite"))
async def cmd_family(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    members = await repo.get_household_members(household.id)
    member_lines = [
        f"• <b>{html.escape(m.display_name)}</b>" for m in members
    ]
    text = (
        f"🏠 <b>{html.escape(household.name)}</b>\n\n"
        f"🔑 <b>Invite Code:</b> <code>{household.invite_code}</code>\n"
        f"💱 <b>Currency:</b> <code>{html.escape(household.currency)}</code>\n\n"
        f"👨‍👩‍👧‍👦 <b>Household Members ({len(members)}):</b>\n"
        + "\n".join(member_lines)
        + "\n\n"
        f"🔗 <b>How to link family members:</b>\n"
        f"1. Send them this command to run in private chat with the bot:\n"
        f"   <code>/join {household.invite_code}</code>\n"
        f"2. Or add this bot to your Family Telegram Group and send:\n"
        f"   <code>/join {household.invite_code}</code>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("join"))
async def cmd_join(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    member: Member,
) -> None:
    if not command.args or not command.args.strip():
        await message.answer(
            "Usage: <code>/join FAM-XXXXXX</code>\n"
            "Ask your family member to run /family to get their invite code.",
            parse_mode="HTML",
        )
        return

    code = command.args.strip().upper()
    is_group = message.chat.type in ("group", "supergroup")
    target = await repo.join_household_by_code(
        telegram_user_id=member.telegram_user_id,
        display_name=member.display_name,
        invite_code=code,
        chat_id=message.chat.id,
        is_group=is_group,
    )
    if target is None:
        await message.answer(
            f"❌ Invite code <code>{html.escape(code)}</code> not found. "
            f"Please check the code with /family.",
            parse_mode="HTML",
        )
        return

    members = await repo.get_household_members(target.id)
    names = ", ".join(html.escape(m.display_name) for m in members)
    scope_note = (
        "This group chat and your account are now linked to"
        if is_group
        else "You have joined"
    )
    await message.answer(
        f"✅ <b>Joined Family Ledger!</b>\n\n"
        f"{scope_note} <b>{html.escape(target.name)}</b> (<code>{target.invite_code}</code>).\n"
        f"👨‍👩‍👧‍👦 <b>Members ({len(members)}):</b> {names}",
        parse_mode="HTML",
    )


@router.message(Command("currency"))
async def cmd_currency(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not command.args or not command.args.strip():
        await message.answer(
            f"Current currency symbol: <code>{html.escape(household.currency)}</code>\n"
            f"To change it, send e.g. <code>/currency S$</code> or <code>/currency €</code>",
            parse_mode="HTML",
        )
        return

    new_cur = command.args.strip()[:10]
    updated = await repo.set_household_currency(household.id, new_cur)
    cur_display = html.escape(updated.currency if updated else new_cur)
    await message.answer(
        f"✅ Household currency updated to <b>{cur_display}</b>",
        parse_mode="HTML",
    )


@router.message(Command("auth", "login", "unlock", "pin", "passphrase"))
async def cmd_already_authenticated(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        "✅ <b>Already Unlocked</b>\n"
        "Your account is already authorized to use this bot. "
        "Use /logout if you wish to lock your session.",
        parse_mode="HTML",
    )


@router.message(Command("logout", "lock"))
async def cmd_logout(
    message: Message,
    repo: ExpenseRepo,
    member: Member,
) -> None:
    await repo.revoke_user_authorization(member.telegram_user_id)
    await message.answer(
        "🔒 <b>Session Locked</b>\n"
        "You have been logged out. Enter the passphrase or send "
        "<code>/auth &lt;passphrase&gt;</code> to unlock again.",
        parse_mode="HTML",
    )


