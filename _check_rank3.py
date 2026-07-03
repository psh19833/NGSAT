#!/usr/bin/env python3
import asyncio
from core.config import load_config
from data.adapters.kis.adapter import KisAdapter
from datetime import datetime

async def main():
    cfg = load_config()
    adapter = KisAdapter(
        app_key=cfg.kis.app_key,
        app_secret=cfg.kis.app_secret,
        account_no=cfg.kis.account_no,
        account_product_code=cfg.kis.account_product_code,
        base_url=cfg.kis.base_url,
    )
    # КИS example-based params: no fid_input_date_1!
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",  # all include (test)
        "FID_INPUT_PRICE_1": "0",
        "FID_INPUT_PRICE_2": "1000000",
        "FID_VOL_CNT": "100000",
    }
    resp = await adapter._http.get("volume_rank", params=params)
    if resp.success:
        items = resp.data.get("output") or []
        print(f"Result: {len(items)} stocks")
        for o in items[:5]:
            print(f"  {o.get('name','')}({o.get('code','')})")
    else:
        print(f"Fail: {resp.msg1[:100]}")
    await adapter.close()

asyncio.run(main())
