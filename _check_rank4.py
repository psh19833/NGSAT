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
    resp = await adapter._http.get("volume_rank", params={
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "0",
        "FID_INPUT_PRICE_2": "1000000",
        "FID_VOL_CNT": "100000",
    })
    print(f"rt_cd={resp.rt_cd} msg_cd={resp.msg_cd} msg1={resp.msg1}")
    if resp.data:
        print(f"keys: {list(resp.data.keys())}")
        for k, v in resp.data.items():
            if isinstance(v, list):
                print(f"  {k}: list[{len(v)}]")
                if v:
                    print(f"  first: {v[0]}")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={list(v.keys())}")
            else:
                print(f"  {k}: {str(v)[:80]}")
    await adapter.close()

asyncio.run(main())
