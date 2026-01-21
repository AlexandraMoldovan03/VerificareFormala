
const IS_VERCEL = location.hostname.includes("vercel.app");
const API_BASE = IS_VERCEL ? null : ""; // local uses same-origin

const benchList = document.getElementById("benchList");
const refreshBtn = document.getElementById("refresh");

const runMeta = document.getElementById("runMeta");
const metricsEl = document.getElementById("metrics");
const logEl = document.getElementById("log");

const variantSelect = document.getElementById("variantSelect");
const compareA = document.getElementById("compareA");
const compareB = document.getElementById("compareB");
const compareBox = document.getElementById("compareBox");

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");

let variants = [];

function fmtBytes(n){
  if(n == null) return "-";
  const units = ["B","KB","MB","GB"];
  let i=0, x=n;
  while(x>=1024 && i<units.length-1){ x/=1024; i++; }
  return `${x.toFixed(i===0?0:1)} ${units[i]}`;
}
function fmtNum(n){
  if(n == null) return "—";
  return n.toLocaleString("en-US");
}
function fmtFloat(n, d=2){
  if(n == null || Number.isNaN(n)) return "—";
  return n.toFixed(d);
}

function setStatus(state, text){
  statusDot.className = `dot ${state}`;
  statusText.textContent = text;
}

function metricKV(k,v,cls=""){
  const div = document.createElement("div");
  div.className = "kv";
  div.innerHTML = `<div class="k">${k}</div><div class="v ${cls}">${v}</div>`;
  return div;
}

function renderMetrics(stats){
  metricsEl.innerHTML = "";
  if(!stats){
    metricsEl.classList.add("empty");
    metricsEl.textContent = "—";
    return;
  }
  metricsEl.classList.remove("empty");

  const res = stats.result || "UNKNOWN";
  const resCls = res.includes("UNSAT") ? "bad" : (res.includes("SAT") ? "good" : "warn");

  metricsEl.appendChild(metricKV("Result", res, resCls));
  metricsEl.appendChild(metricKV("CPU time (s)", fmtFloat(stats.cpu_time_s, 4)));

  metricsEl.appendChild(metricKV("Conflicts", fmtNum(stats.conflicts)));
  metricsEl.appendChild(metricKV("Decisions", fmtNum(stats.decisions)));
  metricsEl.appendChild(metricKV("Propagations", fmtNum(stats.propagations)));

  metricsEl.appendChild(metricKV("Decisions/sec", fmtFloat(stats.decisions_per_sec, 2)));
  metricsEl.appendChild(metricKV("Props/sec", fmtFloat(stats.props_per_sec, 2)));
  metricsEl.appendChild(metricKV("Conflicts/sec", fmtFloat(stats.conflicts_per_sec, 2)));

  metricsEl.appendChild(metricKV("ns/prop", fmtFloat(stats.ns_per_prop, 2)));
  metricsEl.appendChild(metricKV("ns/decision", fmtFloat(stats.ns_per_decision, 2)));
}

function renderCompareEmpty(msg="No comparison yet."){
  compareBox.classList.add("empty");
  compareBox.textContent = msg;
}

function classifyDelta(metric, val){
  if(val == null) return "deltaWarn";
  // cpu_time_pct: negative is good (faster)
  // ns_per_prop_pct: negative is good (less ns)
  // *_per_sec_pct: positive is good (more throughput)
  if(metric === "cpu_time_pct" || metric === "ns_per_prop_pct"){
    return val < 0 ? "deltaGood" : (val > 0 ? "deltaBad" : "deltaWarn");
  }
  // throughput
  return val > 0 ? "deltaGood" : (val < 0 ? "deltaBad" : "deltaWarn");
}

function fmtPct(v){
  if(v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function renderCompareResult(payload){
  const a = payload.a;
  const b = payload.b;
  const d = payload.delta || {};

  const lines = [];
  lines.push(`A: ${a.label}`);
  lines.push(`B: ${b.label}`);
  lines.push("");

  const cpu = d.cpu_time_pct;
  const props = d.props_per_sec_pct;
  const decs = d.decisions_per_sec_pct;
  const nsprop = d.ns_per_prop_pct;

  const cpuCls = classifyDelta("cpu_time_pct", cpu);
  const propsCls = classifyDelta("props_per_sec_pct", props);
  const decsCls = classifyDelta("decisions_per_sec_pct", decs);
  const nsCls = classifyDelta("ns_per_prop_pct", nsprop);

  lines.push(`CPU time:      A=${fmtFloat(a.stats.cpu_time_s,4)}s  B=${fmtFloat(b.stats.cpu_time_s,4)}s  Δ=${fmtPct(cpu)}`);
  lines.push(`Props/sec:     A=${fmtFloat(a.stats.props_per_sec,2)}   B=${fmtFloat(b.stats.props_per_sec,2)}   Δ=${fmtPct(props)}`);
  lines.push(`Decisions/sec: A=${fmtFloat(a.stats.decisions_per_sec,2)}   B=${fmtFloat(b.stats.decisions_per_sec,2)}   Δ=${fmtPct(decs)}`);
  lines.push(`ns/prop:       A=${fmtFloat(a.stats.ns_per_prop,2)}   B=${fmtFloat(b.stats.ns_per_prop,2)}   Δ=${fmtPct(nsprop)}`);

  compareBox.classList.remove("empty");
  // Insert with lightweight color spans (HTML)
  compareBox.innerHTML = `
${escapeHtml(lines[0])}
${escapeHtml(lines[1])}
<br><br>
${escapeHtml(lines[3].split("Δ=")[0])}Δ=<span class="${cpuCls}">${escapeHtml(lines[3].split("Δ=")[1])}</span><br>
${escapeHtml(lines[4].split("Δ=")[0])}Δ=<span class="${propsCls}">${escapeHtml(lines[4].split("Δ=")[1])}</span><br>
${escapeHtml(lines[5].split("Δ=")[0])}Δ=<span class="${decsCls}">${escapeHtml(lines[5].split("Δ=")[1])}</span><br>
${escapeHtml(lines[6].split("Δ=")[0])}Δ=<span class="${nsCls}">${escapeHtml(lines[6].split("Δ=")[1])}</span>
  `;
}

function escapeHtml(s){
  return String(s)
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

async function loadVariants(){
  const r = await fetch("/api/variants");
  const data = await r.json();
  variants = data.variants || [];

  // fill selects
  variantSelect.innerHTML = "";
  compareA.innerHTML = "";
  compareB.innerHTML = "";

  for(const v of variants){
    const o1 = document.createElement("option");
    o1.value = v.key; o1.textContent = v.label;
    variantSelect.appendChild(o1);

    const o2 = document.createElement("option");
    o2.value = v.key; o2.textContent = v.label;
    compareA.appendChild(o2);

    const o3 = document.createElement("option");
    o3.value = v.key; o3.textContent = v.label;
    compareB.appendChild(o3);
  }

  // defaults
  variantSelect.value = variants.find(x=>x.key==="baseline") ? "baseline" : (variants[0]?.key || "");
  compareA.value = "baseline";
  compareB.value = variants.find(x=>x.key==="variant2") ? "variant2" : (variants[0]?.key || "");
}

async function loadBenchmarks(){
  setStatus("idle", "Loading benchmarks…");
  benchList.innerHTML = "";
  const r = await fetch("/api/benchmarks");
  const data = await r.json();

  for(const b of data.benchmarks){
    const item = document.createElement("div");
    item.className = "benchItem";
    item.innerHTML = `
      <div class="benchLeft">
        <div class="benchName">${b.name}</div>
        <div class="benchMeta">vars=${b.vars ?? "-"} · clauses=${b.clauses ?? "-"} · size=${fmtBytes(b.bytes)}</div>
      </div>
      <div class="benchRight">
        <button class="btn" data-action="run">Run</button>
        <button class="btn" data-action="compare">Compare</button>
      </div>
    `;
    item.querySelector('[data-action="run"]').onclick = () => runBenchmark(b);
    item.querySelector('[data-action="compare"]').onclick = () => compareBenchmark(b);
    benchList.appendChild(item);
  }

  setStatus("idle", "Idle");
}

async function runBenchmark(b){
  renderCompareEmpty();
  renderMetrics(null);
  logEl.textContent = "—";
  logEl.classList.add("empty");

  const variant = variantSelect.value;
  const variantLabel = variants.find(x=>x.key===variant)?.label || variant;

  setStatus("running", `Running (${variantLabel})…`);
  runMeta.textContent = `benchmark=${b.name} · vars=${b.vars ?? "-"} · clauses=${b.clauses ?? "-"} · size=${fmtBytes(b.bytes)} · variant=${variant}`;

  const resp = await fetch(`/api/run/${encodeURIComponent(b.name)}?variant=${encodeURIComponent(variant)}`, { method:"POST" });
  const start = await resp.json();
  if(start.error){
    setStatus("error", start.error);
    logEl.textContent = start.error;
    return;
  }

  const runId = start.run_id;

  while(true){
    const rr = await fetch(`/api/run/${runId}`);
    const state = await rr.json();

    logEl.classList.remove("empty");
    logEl.textContent = state.log || "";
    logEl.scrollTop = logEl.scrollHeight;

    if(state.status === "DONE"){
      setStatus("done", "Done");
      renderMetrics(state.stats);
      break;
    }
    if(state.status === "ERROR"){
      setStatus("error", "Error");
      break;
    }
    await new Promise(res => setTimeout(res, 200));
  }
}

async function compareBenchmark(b){
  renderMetrics(null);
  logEl.textContent = "—";
  logEl.classList.add("empty");

  const a = compareA.value;
  const bb = compareB.value;

  const la = variants.find(x=>x.key===a)?.label || a;
  const lb = variants.find(x=>x.key===bb)?.label || bb;

  setStatus("running", `Comparing (A vs B)…`);
  runMeta.textContent = `benchmark=${b.name} · vars=${b.vars ?? "-"} · clauses=${b.clauses ?? "-"} · A=${la} · B=${lb}`;

  renderCompareEmpty("Comparison queued…");

  const resp = await fetch(`/api/compare/${encodeURIComponent(b.name)}?a=${encodeURIComponent(a)}&b=${encodeURIComponent(bb)}`, { method:"POST" });
  const start = await resp.json();
  if(start.error){
    setStatus("error", start.error);
    renderCompareEmpty(start.error);
    return;
  }

  const compareId = start.compare_id;

  while(true){
    const rr = await fetch(`/api/compare/${compareId}`);
    const state = await rr.json();

    // show combined logs
    logEl.classList.remove("empty");
    logEl.textContent = state.log || "";
    logEl.scrollTop = logEl.scrollHeight;

    if(state.status === "DONE"){
      setStatus("done", "Compare done");
      renderCompareResult(state.result);
      break;
    }
    if(state.status === "ERROR"){
      setStatus("error", "Compare error");
      renderCompareEmpty(state.log || "Compare failed.");
      break;
    }
    await new Promise(res => setTimeout(res, 250));
  }
}

refreshBtn.onclick = loadBenchmarks;

(async function init(){
  renderCompareEmpty();
  await loadVariants();
  await loadBenchmarks();
})();
