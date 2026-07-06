#!/usr/bin/env python3
"""Send daily report via Telegram."""
import asyncio, json, urllib.request, urllib.parse
from datetime import datetime, timezone
from data.repository import TradeRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from collections import defaultdict
from core.config import load_config

# 1. Get today's trades
engine = create_engine('sqlite:///data/ngsat.db')
Session = sessionmaker(bind=engine)
session = Session()
repo = TradeRepository(session)

today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
trades = repo.get_trades_by_date(today)
session.close()

buy_count = sum(1 for t in trades if t.side == 'buy')
sell_count = sum(1 for t in trades if t.side == 'sell')

# 2. Calculate realized P&L
buy_by_code = defaultdict(list)
sell_by_code = defaultdict(list)
for t in trades:
    if t.side == 'buy':
        buy_by_code[t.code].append(t)
    else:
        sell_by_code[t.code].append(t)

realized_pnl = 0.0
detail_lines = []
all_codes = set(list(buy_by_code.keys()) + list(sell_by_code.keys()))
for code in all_codes:
    buys = buy_by_code.get(code, [])
    sells = sell_by_code.get(code, [])
    total_buy_qty = sum(b.quantity for b in buys)
    total_buy_amt = sum(b.amount for b in buys)
    total_sell_qty = sum(s.quantity for s in sells)
    total_sell_amt = sum(s.amount for s in sells)
    matched_qty = min(total_buy_qty, total_sell_qty)
    if matched_qty > 0 and total_buy_qty > 0:
        avg_buy = total_buy_amt / total_buy_qty
        avg_sell = total_sell_amt / total_sell_qty
        pnl = (avg_sell - avg_buy) * matched_qty
        realized_pnl += pnl
        name = buys[0].name if buys else sells[0].name
        detail_lines.append(
            "%s(%s): %d주 (매수 %.0f → 매도 %.0f) = %+.0f원" % (
                name, code, total_buy_qty, avg_buy, avg_sell, pnl))

# 3. Build message
msg = "📋 일일 보고 (%s)\n──────────\n" % today
msg += "거래: %d건 (매수 %d / 매도 %d)\n" % (len(trades), buy_count, sell_count)
msg += "승률: %.1f%%\n" % repo.get_win_rate(today)
pnl_emoji = "📈" if realized_pnl >= 0 else "📉"
msg += "%s 손익: %+.0f원\n" % (pnl_emoji, realized_pnl)
msg += "잔고: 2,087,420원\n"

if detail_lines:
    msg += "\n📌 상세\n"
    for line in detail_lines:
        msg += "  " + line + "\n"

# 4. Send via Telegram
cfg = load_config()
token = cfg.telegram.bot_token
chat_id = cfg.telegram.chat_id

url = "https://api.telegram.org/bot%s/sendMessage" % token
data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
resp = urllib.request.urlopen(url, data=data, timeout=10)
result = json.loads(resp.read())
if result.get("ok"):
    print("✅ Telegram 발송 성공!")
else:
    print("❌ Telegram 발송 실패:", result)
