#!/usr/bin/env python3
import asyncio
from core.config import load_config
from data.adapters.kis.adapter import KisAdapter

async def main():
    cfg = load_config()
    adapter = KisAdapter(
        app_key=cfg.kis.app_key,
        app_secret=cfg.kis.app_secret,
        account_no=cfg.kis.account_no,
        account_product_code=cfg.kis.account_product_code,
        base_url=cfg.kis.base_url,
    )
    rank = await adapter.get_volume_rank()
    print(f"Volume rank: {len(rank)} stocks")
    etf_count = sum(1 for r in rank if r.get('code','').startswith('Q'))
    print(f"  Q-prefix: {etf_count}")
    for r in rank[:10]:
        print(f"  {r.get('name','')}({r.get('code','')})")
    await adapter.close()

asyncio.run(main())
