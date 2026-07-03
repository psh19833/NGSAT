#!/usr/bin/env python3
import asyncio
import json
from core.config import load_config
from data.adapters.kis.client import KisHttpClient
from data.adapters.kis.token_manager import KisTokenManager

async def main():
    cfg = load_config()
    token_mgr = KisTokenManager(cfg.kis.app_key, cfg.kis.app_secret, cfg.kis.base_url)
    client = KisHttpClient(cfg.kis.app_key, cfg.kis.app_secret, cfg.kis.base_url, token_manager=token_mgr)
    
    resp = await client.get("volume_rank", params={
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
    print(f"rt_cd={resp.rt_cd} msg_cd={resp.msg_cd}")
    print(f"raw keys: {list(resp.raw.keys()) if resp.raw else 'none'}")
    for k, v in resp.raw.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
            if v:
                print(f"    first: {json.dumps(v[0], ensure_ascii=False)[:200]}")
                print(f"    keys: {list(v[0].keys())}")
        else:
            val = str(v)[:80]
            print(f"  {k}: {val}")
    
    await client.close()
    await token_mgr.close()

asyncio.run(main())
