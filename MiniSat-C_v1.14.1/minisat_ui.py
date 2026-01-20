#!/usr/bin/env python3
import os
import re
import sys
import time
import csv
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, Dict, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

console = Console()

@dataclass
class DimacsInfo:
    vars: Optional[int] = None
    clauses: Optional[int] = None

@dataclass
class MiniSatStats:
    result: str = "UNKNOWN"              # SATISFIABLE / UNSATISFIABLE / UNKNOWN
    cpu_time_s: Optional[float] = None
    conflicts: Optional[int] = None
    decisions: Optional[int] = None
    propagations: Optional[int] = None
    # derived
    decisions_per_sec: Optional[float] = None
    props_per_sec: Optional[float] = None
    conflicts_per_sec: Optional[float] = None
    ns_per_prop: Optional[float] = None
    ns_per_decision: Optional[float] = None

def parse_dimacs_header(path: str) -> DimacsInfo:
    info = DimacsInfo()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("p cnf"):
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        info.vars = int(parts[2])
                        info.clauses = int(parts[3])
                    break
    except Exception:
        pass
    return info

def parse_minisat_output(out: str) -> MiniSatStats:
    s = MiniSatStats()

    # Result
    if "UNSATISFIABLE" in out:
        s.result = "UNSATISFIABLE"
    elif "SATISFIABLE" in out:
        s.result = "SATISFIABLE"

    # Common MiniSat lines (depending on version/flags)
    # CPU time              : 0.12 s
    m = re.search(r"CPU time\s*:\s*([0-9]*\.?[0-9]+)\s*s", out)
    if m:
        s.cpu_time_s = float(m.group(1))

    # conflicts             : 1234
    m = re.search(r"conflicts\s*:\s*([0-9,]+)", out)
    if m:
        s.conflicts = int(m.group(1).replace(",", ""))

    # decisions             : 12345
    m = re.search(r"decisions\s*:\s*([0-9,]+)", out)
    if m:
        s.decisions = int(m.group(1).replace(",", ""))

    # propagations          : 1234567
    m = re.search(r"propagations\s*:\s*([0-9,]+)", out)
    if m:
        s.propagations = int(m.group(1).replace(",", ""))

    # derived
    if s.cpu_time_s and s.cpu_time_s > 0:
        if s.decisions is not None:
            s.decisions_per_sec = s.decisions / s.cpu_time_s
            s.ns_per_decision = (s.cpu_time_s * 1e9 / s.decisions) if s.decisions > 0 else None
        if s.propagations is not None:
            s.props_per_sec = s.propagations / s.cpu_time_s
            s.ns_per_prop = (s.cpu_time_s * 1e9 / s.propagations) if s.propagations > 0 else None
        if s.conflicts is not None:
            s.conflicts_per_sec = s.conflicts / s.cpu_time_s

    return s

def run_minisat(exec_path: str, cnf_path: str) -> Tuple[int, str, str]:
    # Minisat usage typically: minisat <input.cnf> [output]
    proc = subprocess.Popen(
        [exec_path, cnf_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    out, err = proc.communicate()
    return proc.returncode, out, err

def human_int(x: Optional[int]) -> str:
    if x is None:
        return "-"
    return f"{x:,}"

def human_float(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:.{digits}f}"

def build_metrics_table(stats: MiniSatStats) -> Table:
    t = Table(title="MiniSat Metrics", expand=True)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")

    t.add_row("Result", stats.result)
    t.add_row("CPU time (s)", human_float(stats.cpu_time_s, 4))
    t.add_row("Conflicts", human_int(stats.conflicts))
    t.add_row("Decisions", human_int(stats.decisions))
    t.add_row("Propagations", human_int(stats.propagations))

    t.add_section()
    t.add_row("Decisions/sec", human_float(stats.decisions_per_sec, 2))
    t.add_row("Propagations/sec", human_float(stats.props_per_sec, 2))
    t.add_row("Conflicts/sec", human_float(stats.conflicts_per_sec, 2))
    t.add_row("ns/prop", human_float(stats.ns_per_prop, 2))
    t.add_row("ns/decision", human_float(stats.ns_per_decision, 2))
    return t

def append_csv(csv_path: str, cnf: str, dimacs: DimacsInfo, stats: MiniSatStats) -> None:
    is_new = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow([
                "timestamp", "benchmark", "vars", "clauses",
                "result", "cpu_time_s", "conflicts", "decisions", "propagations",
                "decisions_per_sec", "props_per_sec", "conflicts_per_sec", "ns_per_prop", "ns_per_decision"
            ])
        w.writerow([
            int(time.time()), os.path.basename(cnf), dimacs.vars, dimacs.clauses,
            stats.result, stats.cpu_time_s, stats.conflicts, stats.decisions, stats.propagations,
            stats.decisions_per_sec, stats.props_per_sec, stats.conflicts_per_sec, stats.ns_per_prop, stats.ns_per_decision
        ])

def main():
    folder = os.getcwd()
    exec_path = os.path.join(folder, "minisat")
    if not os.path.exists(exec_path):
        console.print("[red]Nu găsesc executabilul ./minisat în folderul curent.[/red]")
        sys.exit(1)

    cnfs = [f for f in os.listdir(folder) if f.endswith(".cnf")]
    cnfs.sort()
    if not cnfs:
        console.print("[yellow]Nu am găsit fișiere .cnf în folder. Pune benchmark-urile aici.[/yellow]")
        sys.exit(1)

    # simple selection
    console.print("\n[bold]Benchmarks detectate:[/bold]")
    for i, f in enumerate(cnfs, 1):
        console.print(f"  {i}. {f}")
    choice = console.input("\nAlege benchmark (număr): ").strip()
    try:
        idx = int(choice) - 1
        cnf = cnfs[idx]
    except Exception:
        console.print("[red]Selecție invalidă.[/red]")
        sys.exit(1)

    cnf_path = os.path.join(folder, cnf)
    dimacs = parse_dimacs_header(cnf_path)

    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=2),
        Layout(name="bottom", ratio=1),
    )
    layout["top"].split_row(
        Layout(name="info", ratio=1),
        Layout(name="log", ratio=2),
    )

    info_text = Text()
    info_text.append(f"Executable: {exec_path}\n", style="bold")
    info_text.append(f"Benchmark:  {cnf}\n", style="bold")
    info_text.append(f"Vars:       {dimacs.vars if dimacs.vars is not None else '-'}\n")
    info_text.append(f"Clauses:    {dimacs.clauses if dimacs.clauses is not None else '-'}\n")
    info_panel = Panel(info_text, title="Run Info", border_style="cyan")

    log_text = Text("Running minisat...\n", style="green")
    log_panel = Panel(log_text, title="MiniSat Output", border_style="magenta")

    layout["info"].update(info_panel)
    layout["log"].update(log_panel)
    layout["bottom"].update(Panel("Waiting for results...", title="Metrics", border_style="yellow"))

    with Live(layout, refresh_per_second=10, screen=True):
        rc, out, err = run_minisat(exec_path, cnf_path)
        log_text = Text()
        log_text.append(out if out else "", style="white")
        if err:
            log_text.append("\n[stderr]\n", style="bold red")
            log_text.append(err, style="red")
        layout["log"].update(Panel(log_text, title=f"MiniSat Output (rc={rc})", border_style="magenta"))

        stats = parse_minisat_output(out + "\n" + err)
        layout["bottom"].update(Panel(build_metrics_table(stats), border_style="yellow"))

        append_csv("results.csv", cnf, dimacs, stats)

        time.sleep(0.5)

    console.print("\n[bold green]Done.[/bold green] Rezultate salvate în [bold]results.csv[/bold].")

if __name__ == "__main__":
    main()
