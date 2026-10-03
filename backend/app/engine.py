"""ctypes bridge to the C engine, with a pure-Python fallback (same algorithm) if gcc/.so is unavailable."""
import ctypes
import os
import subprocess
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "engine", "graph_engine.c")
LIB = os.path.join(ROOT, "engine", "libsentinel.so")


def _load():
    try:
        if not os.path.exists(LIB) or os.path.getmtime(LIB) < os.path.getmtime(SRC):
            subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-o", LIB, SRC], check=True, capture_output=True)
        lib = ctypes.CDLL(LIB)
        lib.trace_flows.argtypes = [ctypes.c_int, ctypes.c_int] + [ctypes.c_void_p] * 5 + [ctypes.c_longlong] + [ctypes.c_void_p] * 5
        lib.trace_flows.restype = None
        return lib
    except Exception:
        return None


_lib = _load()
ENGINE = "c" if _lib else "python"


def _trace_py(n, src, dst, amt, ts, cash, window):
    adj, best = defaultdict(list), {}
    for e in range(len(src)):
        adj[int(src[e])].append(e)
        d = int(dst[e])
        if d not in best or amt[e] > amt[best[d]]:
            best[d] = e
    reach, depth = np.zeros(n, np.int32), np.zeros(n, np.int32)
    cash_amt, span, dwell = np.zeros(n), np.full(n, -1, np.int64), np.full(n, -1, np.int64)
    for v, be in best.items():
        t0 = int(ts[be]); tend = t0 + window
        arrive, dep, q = {v: t0}, {v: 0}, [v]
        while q:
            u = q.pop(0)
            for e in adj[u]:
                t = int(ts[e])
                if t < arrive[u] or t > tend:
                    continue
                w = int(dst[e])
                if u == v and (dwell[v] < 0 or t - t0 < dwell[v]):
                    dwell[v] = t - t0
                if cash[w]:
                    cash_amt[v] += amt[e]
                    span[v] = max(span[v], t - t0)
                if w not in arrive:
                    arrive[w], dep[w] = t, dep[u] + 1
                    reach[v] += 1
                    depth[v] = max(depth[v], dep[w])
                    if not cash[w]:
                        q.append(w)
    return dict(reach=reach, depth=depth, cash_amt=cash_amt, span=span, dwell=dwell)


def trace_flows(n, src, dst, amt, ts, cash, window, force_python=False):
    src = np.ascontiguousarray(src, np.int32); dst = np.ascontiguousarray(dst, np.int32)
    amt = np.ascontiguousarray(amt, np.float64); ts = np.ascontiguousarray(ts, np.int64)
    cash = np.ascontiguousarray(cash, np.uint8)
    if _lib is None or force_python:
        return _trace_py(n, src, dst, amt, ts, cash, window)
    reach, depth = np.zeros(n, np.int32), np.zeros(n, np.int32)
    cash_amt, span, dwell = np.zeros(n), np.zeros(n, np.int64), np.zeros(n, np.int64)
    _lib.trace_flows(n, len(src), src.ctypes.data, dst.ctypes.data, amt.ctypes.data, ts.ctypes.data,
                     cash.ctypes.data, int(window), reach.ctypes.data, depth.ctypes.data,
                     cash_amt.ctypes.data, span.ctypes.data, dwell.ctypes.data)
    return dict(reach=reach, depth=depth, cash_amt=cash_amt, span=span, dwell=dwell)
