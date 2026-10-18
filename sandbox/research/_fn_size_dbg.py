import platform; platform._wmi=None
import os
os.environ.update(CFD_BROKER="fundednext", CFD_SPLIT="2024-2025", CFD_OOS_END="2026-10-13")
from sandbox.research import fundednext_combined_strategies as fn
from sandbox.research import _fundednext_parity as fp
base=fn.base; orig=base.size
WANT={("gbpjpy:macd_hist@15m-barlb-off",1775619900),("gbpaud:failed_break@30m-early_morning",1777456800),("gbpjpy:half_life@30m-ny_open",1782315000)}
def size(sleeve, equity, trade, ctx, scale, sizing_cap, risk_scale, shown=None, vol_mult=1.0, force_minimum=False):
    out=orig(sleeve, equity, trade, ctx, scale, sizing_cap, risk_scale, shown, vol_mult, force_minimum)
    if (sleeve, trade["entry_ts"]) in WANT:
        spec=ctx["cfg"]; raw=equity*fn.ef.RISK_FRACTION*scale.get(sleeve,1)*risk_scale*vol_mult/(trade["distance"]*spec["multiplier"])
        print("SIZE", sleeve, trade["entry_ts"], "equity", repr(equity), "mult", repr(vol_mult), "dist", repr(trade["distance"]), "raw/step", repr(raw/spec["volume_step"]), "lots", out[0])
    return out
base.size=size
s=fp.seal()
state=fn.load_state(s["members"], fn._stamp(fn.LOG_START), fn.ef.OOS_END)
fn.replay(state, s["risk"], lo=fn._stamp("2026-01-01"), hi=fn.ef.OOS_END)
