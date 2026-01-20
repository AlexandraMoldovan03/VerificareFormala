import os
import re
import time
import csv
import uuid
import shutil
import threading
import subprocess
from typing import Dict, Optional, List, Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(APP_DIR, "build")
RESULTS_CSV = os.path.join(APP_DIR, "results.csv")

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")

# ========= Variants (baseline + variant2) =========
VARIANTS = {
    "baseline": {
        "label": "Baseline (solver.c)",
        "solver_src": "solver.c",
        "build_dir": os.path.join(BUILD_DIR, "baseline"),
        "target": "minisat",      # builds ./minisat
        "make_target": "s",       # standard (O3 -DNDEBUG)
    },
    "variant2": {
        "label": "Variant2 (solver2.c)",
        "solver_src": "solver2.c",
        "build_dir": os.path.join(BUILD_DIR, "variant2"),
        "target": "minisat",
        "make_target": "s",
    },
}

# runtime storage
runs: Dict[str, Dict[str, Any]] = {}       # run_id -> {status, log, stats, meta}
compares: Dict[str, Dict[str, Any]] = {}   # compare_id -> {status, ...}

# ========= Helpers =========

def parse_dimacs_header(path: str):
    vars_, clauses = None, None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("p cnf"):
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        vars_ = int(parts[2])
                        clauses = int(parts[3])
                    break
    except Exception:
        pass
    return vars_, clauses

def parse_minisat_output(text: str):
    result = "UNKNOWN"
    if "UNSATISFIABLE" in text:
        result = "UNSATISFIABLE"
    elif "SATISFIABLE" in text:
        result = "SATISFIABLE"

    def find_int(pattern: str) -> Optional[int]:
        m = re.search(pattern, text)
        if not m:
            return None
        return int(m.group(1).replace(",", ""))

    def find_float(pattern: str) -> Optional[float]:
        m = re.search(pattern, text)
        if not m:
            return None
        return float(m.group(1))

    cpu = find_float(r"CPU time\s*:\s*([0-9]*\.?[0-9]+)\s*s")
    conflicts = find_int(r"conflicts\s*:\s*([0-9,]+)")
    decisions = find_int(r"decisions\s*:\s*([0-9,]+)")
    props = find_int(r"propagations\s*:\s*([0-9,]+)")

    stats = {
        "result": result,
        "cpu_time_s": cpu,
        "conflicts": conflicts,
        "decisions": decisions,
        "propagations": props,
        "decisions_per_sec": None,
        "props_per_sec": None,
        "conflicts_per_sec": None,
        "ns_per_prop": None,
        "ns_per_decision": None,
    }

    if cpu and cpu > 0:
        if decisions is not None and decisions > 0:
            stats["decisions_per_sec"] = decisions / cpu
            stats["ns_per_decision"] = (cpu * 1e9) / decisions
        if props is not None and props > 0:
            stats["props_per_sec"] = props / cpu
            stats["ns_per_prop"] = (cpu * 1e9) / props
        if conflicts is not None:
            stats["conflicts_per_sec"] = conflicts / cpu

    return stats

def append_csv(benchmark: str, vars_: Optional[int], clauses: Optional[int], variant_key: str, variant_label: str, stats: Dict):
    is_new = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow([
                "timestamp", "benchmark", "vars", "clauses",
                "variant_key", "variant_label",
                "result", "cpu_time_s", "conflicts", "decisions", "propagations",
                "decisions_per_sec", "props_per_sec", "conflicts_per_sec", "ns_per_prop", "ns_per_decision"
            ])
        w.writerow([
            int(time.time()), benchmark, vars_, clauses,
            variant_key, variant_label,
            stats.get("result"), stats.get("cpu_time_s"), stats.get("conflicts"),
            stats.get("decisions"), stats.get("propagations"),
            stats.get("decisions_per_sec"), stats.get("props_per_sec"),
            stats.get("conflicts_per_sec"), stats.get("ns_per_prop"), stats.get("ns_per_decision")
        ])

def safe_rm_tree(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)

def copy_project_sources(dst_dir: str, solver_src_name: str) -> Optional[str]:
    """
    Copiază proiectul în dst_dir astfel încât:
    - copiază toate .c și .h din root, dar EXCLUDE solver*.c
    - copiază solver_src_name (ex solver2.c) ca solver.c
    - copiază Makefile și depend.mak dacă există
    """
    os.makedirs(dst_dir, exist_ok=True)

    solver_src_path = os.path.join(APP_DIR, solver_src_name)
    if not os.path.exists(solver_src_path):
        return f"Lipsește fișierul: {solver_src_name}"

    # copy Makefile + depend.mak if exists
    for fname in ["Makefile", "depend.mak"]:
        src = os.path.join(APP_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst_dir, fname))

    # copy headers
    for fname in os.listdir(APP_DIR):
        if fname.endswith(".h"):
            shutil.copy2(os.path.join(APP_DIR, fname), os.path.join(dst_dir, fname))

    # copy .c excluding solver*.c
    for fname in os.listdir(APP_DIR):
        if fname.endswith(".c"):
            if fname.startswith("solver") and fname.endswith(".c"):
                # exclude solver.c, solver2.c, solverX.c etc.
                continue
            shutil.copy2(os.path.join(APP_DIR, fname), os.path.join(dst_dir, fname))

    # copy chosen solver as solver.c
    shutil.copy2(solver_src_path, os.path.join(dst_dir, "solver.c"))
    return None

def ensure_built(variant_key: str, force_rebuild: bool = False) -> Optional[str]:
    if variant_key not in VARIANTS:
        return "Variant invalid."

    v = VARIANTS[variant_key]
    bdir = v["build_dir"]
    target = v["target"]
    make_target = v["make_target"]

    exe_path = os.path.join(bdir, target)
    if (not force_rebuild) and os.path.exists(exe_path):
        return None

    # rebuild: clean folder
    safe_rm_tree(bdir)
    os.makedirs(bdir, exist_ok=True)

    err = copy_project_sources(bdir, v["solver_src"])
    if err:
        return err

    try:
        # build standard: make s (O3 -DNDEBUG)
        r = subprocess.run(["make", "clean"], cwd=bdir, capture_output=True, text=True)
        r = subprocess.run(["make", make_target], cwd=bdir, capture_output=True, text=True)
        if r.returncode != 0:
            return f"make failed:\n{r.stdout}\n{r.stderr}"

        if not os.path.exists(exe_path):
            return f"Build ok, dar nu găsesc executabilul {target} în {bdir}"
        return None
    except Exception as e:
        return str(e)

def run_minisat_stream(exe_path: str, cnf_path: str, on_line):
    proc = subprocess.Popen(
        [exe_path, cnf_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    output_lines: List[str] = []
    for line in proc.stdout:
        output_lines.append(line)
        on_line("".join(output_lines))
    rc = proc.wait()
    return rc, "".join(output_lines)

def compute_compare_delta(a_stats: Dict, b_stats: Dict):
    """
    Returnează un dict cu deltas procentuale:
    - cpu_time_s: negative => B faster (good)
    - props_per_sec: positive => B better (good)
    - ns_per_prop: negative => B better (good)
    """
    def pct(new, old):
        if old is None or old == 0 or new is None:
            return None
        return (new - old) / old * 100.0

    delta = {
        "cpu_time_pct": pct(b_stats.get("cpu_time_s"), a_stats.get("cpu_time_s")),
        "props_per_sec_pct": pct(b_stats.get("props_per_sec"), a_stats.get("props_per_sec")),
        "decisions_per_sec_pct": pct(b_stats.get("decisions_per_sec"), a_stats.get("decisions_per_sec")),
        "ns_per_prop_pct": pct(b_stats.get("ns_per_prop"), a_stats.get("ns_per_prop")),
    }
    return delta


# ========= Background tasks =========

def run_task(run_id: str, variant_key: str, benchmark: str, cnf_path: str, vars_: Optional[int], clauses: Optional[int]):
    try:
        err = ensure_built(variant_key)
        if err:
            runs[run_id]["status"] = "ERROR"
            runs[run_id]["log"] += f"[build error] {err}\n"
            return

        exe_path = os.path.join(VARIANTS[variant_key]["build_dir"], VARIANTS[variant_key]["target"])

        def on_line(full_log: str):
            runs[run_id]["log"] = full_log

        rc, full = run_minisat_stream(exe_path, cnf_path, on_line)
        stats = parse_minisat_output(full)

        runs[run_id]["stats"] = stats
        runs[run_id]["status"] = "DONE"
        runs[run_id]["meta"]["exit_code"] = rc

        append_csv(
            benchmark, vars_, clauses,
            variant_key, VARIANTS[variant_key]["label"],
            stats
        )
    except Exception as e:
        runs[run_id]["status"] = "ERROR"
        runs[run_id]["log"] += f"\n[server error] {e}\n"

def compare_task(compare_id: str, benchmark: str, cnf_path: str, vars_: Optional[int], clauses: Optional[int], a: str, b: str):
    try:
        # ensure builds (in advance)
        ea = ensure_built(a)
        eb = ensure_built(b)
        if ea or eb:
            compares[compare_id]["status"] = "ERROR"
            compares[compare_id]["log"] = f"[build error]\nA: {ea}\nB: {eb}\n"
            return

        compares[compare_id]["status"] = "RUNNING"
        compares[compare_id]["log"] = "Running A...\n"

        # Run A
        exe_a = os.path.join(VARIANTS[a]["build_dir"], VARIANTS[a]["target"])
        def on_line_a(full):
            compares[compare_id]["log"] = "Running A...\n" + full
        rc_a, out_a = run_minisat_stream(exe_a, cnf_path, on_line_a)
        stats_a = parse_minisat_output(out_a)

        compares[compare_id]["log"] = compares[compare_id]["log"] + "\nRunning B...\n"

        # Run B
        exe_b = os.path.join(VARIANTS[b]["build_dir"], VARIANTS[b]["target"])
        def on_line_b(full):
            compares[compare_id]["log"] = "Running A...\n" + out_a + "\n\nRunning B...\n" + full
        rc_b, out_b = run_minisat_stream(exe_b, cnf_path, on_line_b)
        stats_b = parse_minisat_output(out_b)

        delta = compute_compare_delta(stats_a, stats_b)

        compares[compare_id]["status"] = "DONE"
        compares[compare_id]["result"] = {
            "a": {"key": a, "label": VARIANTS[a]["label"], "exit_code": rc_a, "stats": stats_a},
            "b": {"key": b, "label": VARIANTS[b]["label"], "exit_code": rc_b, "stats": stats_b},
            "delta": delta,
        }

        # optional: append both to CSV for traceability
        append_csv(benchmark, vars_, clauses, a, VARIANTS[a]["label"], stats_a)
        append_csv(benchmark, vars_, clauses, b, VARIANTS[b]["label"], stats_b)

    except Exception as e:
        compares[compare_id]["status"] = "ERROR"
        compares[compare_id]["log"] += f"\n[server error] {e}\n"


# ========= Routes =========

@app.get("/")
def root():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))

@app.get("/api/variants")
def list_variants():
    return {"variants": [{"key": k, "label": VARIANTS[k]["label"]} for k in VARIANTS]}

@app.get("/api/benchmarks")
def list_benchmarks():
    cnfs = sorted([f for f in os.listdir(APP_DIR) if f.endswith(".cnf")])
    items = []
    for f in cnfs:
        path = os.path.join(APP_DIR, f)
        vars_, clauses = parse_dimacs_header(path)
        size = os.path.getsize(path)
        items.append({"name": f, "vars": vars_, "clauses": clauses, "bytes": size})
    return {"benchmarks": items}

@app.post("/api/build/{variant_key}")
def build_variant(variant_key: str, force: bool = Query(False)):
    err = ensure_built(variant_key, force_rebuild=force)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    return {"ok": True, "variant": variant_key}

@app.post("/api/run/{benchmark}")
def start_run(benchmark: str, variant: str = Query("baseline")):
    cnf_path = os.path.join(APP_DIR, benchmark)
    if not os.path.exists(cnf_path):
        return JSONResponse({"error": "Benchmark inexistent."}, status_code=404)

    if variant not in VARIANTS:
        return JSONResponse({"error": "Variant invalid."}, status_code=400)

    run_id = str(uuid.uuid4())
    vars_, clauses = parse_dimacs_header(cnf_path)

    runs[run_id] = {
        "status": "RUNNING",
        "log": "",
        "stats": None,
        "meta": {
            "benchmark": benchmark,
            "vars": vars_,
            "clauses": clauses,
            "variant_key": variant,
            "variant_label": VARIANTS[variant]["label"],
            "started_at": time.time(),
        },
    }

    t = threading.Thread(
        target=run_task,
        args=(run_id, variant, benchmark, cnf_path, vars_, clauses),
        daemon=True
    )
    t.start()

    return {"run_id": run_id}

@app.get("/api/run/{run_id}")
def poll_run(run_id: str):
    if run_id not in runs:
        return JSONResponse({"error": "run_id invalid."}, status_code=404)
    return runs[run_id]

@app.post("/api/compare/{benchmark}")
def start_compare(
    benchmark: str,
    a: str = Query("baseline"),
    b: str = Query("variant2"),
):
    cnf_path = os.path.join(APP_DIR, benchmark)
    if not os.path.exists(cnf_path):
        return JSONResponse({"error": "Benchmark inexistent."}, status_code=404)
    if a not in VARIANTS or b not in VARIANTS:
        return JSONResponse({"error": "Variant invalid (a/b)."}, status_code=400)

    compare_id = str(uuid.uuid4())
    vars_, clauses = parse_dimacs_header(cnf_path)

    compares[compare_id] = {
        "status": "QUEUED",
        "log": "",
        "meta": {
            "benchmark": benchmark,
            "vars": vars_,
            "clauses": clauses,
            "a": a,
            "b": b,
            "started_at": time.time(),
        },
        "result": None,
    }

    t = threading.Thread(
        target=compare_task,
        args=(compare_id, benchmark, cnf_path, vars_, clauses, a, b),
        daemon=True
    )
    t.start()

    return {"compare_id": compare_id}

@app.get("/api/compare/{compare_id}")
def poll_compare(compare_id: str):
    if compare_id not in compares:
        return JSONResponse({"error": "compare_id invalid."}, status_code=404)
    return compares[compare_id]

@app.get("/api/results.csv")
def download_csv():
    if not os.path.exists(RESULTS_CSV):
        return JSONResponse({"error": "Nu există results.csv încă."}, status_code=404)
    return FileResponse(RESULTS_CSV, filename="results.csv")
