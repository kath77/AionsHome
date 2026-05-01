from __future__ import annotations
"""恢复回家状态：发送家坐标让系统恢复 at_home"""
import asyncio, json, sys
sys.path.insert(0, ".")
from location import load_location_config, save_location_config, process_heartbeat, load_location_status

async def simulate_home():
    cfg = load_location_config()
    old_quiet = cfg.get("quiet_hours_enabled", False)
    cfg["quiet_hours_enabled"] = False
    cfg["enabled"] = True
    save_location_config(cfg)

    r = await process_heartbeat(cfg["home_lng"], cfg["home_lat"], accuracy=10.0, is_gcj02=True, skip_sentinel=True)
    print(f"回家心跳: state={r.get('state')}, addr={r.get('address')}")

    cfg["quiet_hours_enabled"] = old_quiet
    save_location_config(cfg)
    print("\n✅ 已恢复回家状态")

asyncio.run(simulate_home())
