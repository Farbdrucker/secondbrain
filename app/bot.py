"""Telegram bot: accepts messages from one chat, writes inbox entries, replies briefly. No heavy work."""

import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import config, inbox, readlater
from .search import format_results, search

log = logging.getLogger("bot")

QUEUED = "Will be processed in the nightly run."

HELP = (
    "Send me a link (web page, YouTube, Instagram), a PDF or a text note; it is filed into the wiki "
    "in the nightly run.\n\n"
    "/search <words> – find entries containing all words"
)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    cfg = ctx.bot_data["cfg"]
    sent = msg.date.astimezone(cfg.tz)
    items = inbox.classify_text(msg.text or msg.caption or "")
    kinds = []
    for i, (kind, payload, comment) in enumerate(items):
        seq = f"{msg.message_id}" if len(items) == 1 else f"{msg.message_id}-{i + 1}"
        inbox.write(cfg.inbox, inbox.Entry(inbox.make_id(sent, seq), sent.isoformat(), kind, payload,
                                           comment=comment))
        kinds.append("web → read later" if kind == "web" and readlater.matches(payload) else kind)
    await msg.reply_text(f"Received ({', '.join(kinds)}). {QUEUED}")


async def on_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    cfg = ctx.bot_data["cfg"]
    doc = msg.document
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await msg.reply_text("PDF is larger than 20 MB, the Bot API cannot download it. Send a link instead.")
        return
    sent = msg.date.astimezone(cfg.tz)
    entry_id = inbox.make_id(sent, msg.message_id)
    rel = f"raw/{entry_id}.pdf"
    file = await doc.get_file()
    await file.download_to_drive(cfg.brain / rel)
    inbox.write(cfg.inbox, inbox.Entry(entry_id, sent.isoformat(), "pdf", rel, comment=msg.caption or "",
                                       file_name=doc.file_name or ""))
    await msg.reply_text(f"Received (pdf). {QUEUED}")


async def on_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or [])
    if not query:
        await update.effective_message.reply_text("Usage: /search <words>")
        return
    results = search(ctx.bot_data["cfg"].wiki, query)
    await update.effective_message.reply_text(format_results(query, results)[:4000],
                                              disable_web_page_preview=True)


async def on_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP)


async def on_foreign(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    log.warning("ignored message from chat %s (%s); TELEGRAM_CHAT_ID is %s",
                chat.id if chat else "?", chat.username if chat else "?", ctx.bot_data["chat_id"])


async def on_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Not supported. Send text, a link or a PDF.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config.load()
    cfg.ensure_dirs()
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
    if str(chat_id) == os.environ["TELEGRAM_BOT_TOKEN"].split(":")[0]:
        raise SystemExit("TELEGRAM_CHAT_ID is the bot's own ID. Use your user ID (e.g. from @userinfobot).")

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([("search", "Find entries containing all words"),
                                                ("help", "What this bot does")])

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).post_init(post_init).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["chat_id"] = chat_id
    # Everyone except the owner's chat is ignored without a reply.
    owner = filters.Chat(chat_id=chat_id)
    mine = owner & ~filters.COMMAND
    app.add_handler(CommandHandler("search", on_search, filters=owner))
    app.add_handler(CommandHandler(["start", "help"], on_help, filters=owner))
    app.add_handler(MessageHandler(owner & filters.COMMAND, on_help))
    app.add_handler(MessageHandler(mine & filters.TEXT, on_text))
    app.add_handler(MessageHandler(mine & filters.Document.PDF, on_pdf))
    app.add_handler(MessageHandler(mine, on_other))
    app.add_handler(MessageHandler(~filters.Chat(chat_id=chat_id), on_foreign))
    log.info("polling, accepting chat %s", chat_id)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
