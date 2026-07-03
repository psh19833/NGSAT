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
    for trgt_cls, trgt_exls, label in [
        ("111111111", "0", "9-digit target"),
        ("000000000", "0", "9-zero target"),
        ("111111111", "0000000000", "10-zero exclude"),
        ("0", "0", "original"),
    ]:
        resp = await adapter._http.get("volume_rank", params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": trgt_cls,
            "FID_TRGT_EXLS_CLS_CODE": trgt_exls,
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": datetime.now().strftime("%Y%m%d"),
        })
        print(f"\n=== {label} ===")
        print(f"  trgt_cls={trgt_cls!r} trgt_exls={trgt_exls!r}")
        if resp.success:
            items = resp.data.get("output") or resp.data.get("output2") or []
            print(f"  result: {len(items)} stocks")
            for o in items[:3]:
                for k in ['code', 'name', 'volume']:
                    print(f"    {k}={o.get(k, 'N/A')[:20]}")
        else:
            print(f"  fail: {resp.msg1[:100]}")
    await adapter.close()

asyncio.run(main())
