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
    print(f"Rank results: {len(rank)} stocks")
    for r in rank:
        print(f"  {r['name']}({r['code']})")
    await adapter.close()

asyncio.run(main())
