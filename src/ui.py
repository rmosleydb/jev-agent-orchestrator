"""Inline single-page UI for the JEV Dynamic Agent Orchestrator."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>JEV Dynamic Agent Orchestrator</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3;
          --muted:#8b949e; --accent:#f0883e; --good:#3fb950; --dim:#21262d; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:18px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
  .wrap { display:grid; grid-template-columns: 380px 1fr; gap:0; height:calc(100vh - 74px); }
  .left { border-right:1px solid var(--line); padding:18px 20px; overflow:auto; }
  .right { padding:18px 24px; overflow:auto; }
  label { display:block; color:var(--muted); font-size:12px; margin:14px 0 6px;
          text-transform:uppercase; letter-spacing:.04em; }
  textarea, input[type=text] { width:100%; background:var(--dim); color:var(--fg);
          border:1px solid var(--line); border-radius:8px; padding:10px; font:inherit; }
  textarea { min-height:110px; resize:vertical; }
  .row { display:flex; gap:8px; align-items:center; }
  .btn { border:1px solid var(--line); background:var(--dim); color:var(--fg);
         border-radius:8px; padding:9px 14px; cursor:pointer; font:inherit; }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:#1b1000; font-weight:600; }
  .btn:disabled { opacity:.5; cursor:default; }
  .hint { color:var(--muted); font-size:12px; margin-top:8px; }
  .slider-val { color:var(--accent); font-weight:600; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:16px; margin-bottom:16px; }
  .card h2 { margin:0 0 10px; font-size:13px; text-transform:uppercase;
             letter-spacing:.05em; color:var(--muted); }
  .model-pick { font-size:18px; font-weight:700; color:var(--accent); }
  .conf { color:var(--muted); font-size:12px; margin-left:8px; }
  .cands { margin-top:12px; display:flex; flex-direction:column; gap:6px; }
  .cand { display:grid; grid-template-columns:20px 1fr 120px 48px; align-items:center;
          gap:10px; font-size:13px; opacity:.7; }
  .cand.win { opacity:1; }
  .cand .rank { color:var(--muted); font-variant-numeric:tabular-nums; text-align:center; }
  .cand .clabel { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cand.win .clabel { color:var(--good); }
  .cand .cbar { height:6px; background:var(--dim); border-radius:4px; overflow:hidden; }
  .cand .cbar > span { display:block; height:100%; background:var(--accent); }
  .cand.win .cbar > span { background:var(--good); }
  .cand .cpct { font-variant-numeric:tabular-nums; font-size:12px; color:var(--muted); text-align:right; }
  .item { margin:8px 0; }
  .item .top { display:flex; justify-content:space-between; align-items:baseline; }
  .item .name { font-weight:600; }
  .item .name.sel { color:var(--good); }
  .item .name.off { color:var(--muted); }
  .item .desc { color:var(--muted); font-size:12px; margin:2px 0 4px; }
  .bar { height:6px; background:var(--dim); border-radius:4px; overflow:hidden; }
  .bar > span { display:block; height:100%; background:var(--accent); }
  .bar.sel > span { background:var(--good); }
  .pct { font-variant-numeric: tabular-nums; font-size:12px; color:var(--muted); }
  .reply { white-space:pre-wrap; background:var(--dim); border:1px solid var(--line);
           border-radius:8px; padding:14px; }
  .tag { display:inline-block; background:var(--dim); border:1px solid var(--line);
         border-radius:6px; padding:2px 8px; margin:2px 4px 2px 0; font-size:12px; }
  .tag.good { border-color:var(--good); color:var(--good); }
  .tag.bad { border-color:#f85149; color:#f85149; cursor:help; }
  .tag.has-panel { position:relative; }
  .epanel { display:none; position:absolute; left:0; top:calc(100% + 6px); z-index:50;
            width:min(560px, 90vw); background:#0d1117; color:#e6edf3;
            border:1px solid #f85149; border-radius:8px; padding:10px 12px;
            box-shadow:0 8px 28px rgba(0,0,0,.55); white-space:normal;
            font-size:12px; line-height:1.45; text-align:left; cursor:text; }
  .tag.bad.clickable { cursor:pointer; }
  .tag.bad.clickable:hover { background:rgba(248,81,73,.12); }
  /* click-to-open error modal */
  .modal-back { display:none; position:fixed; inset:0; z-index:200;
                background:rgba(0,0,0,.6); }
  .modal-back.open { display:flex; align-items:center; justify-content:center; }
  .modal { width:min(720px, 92vw); max-height:80vh; overflow:auto;
           background:#0d1117; color:#e6edf3; border:1px solid #f85149;
           border-radius:10px; padding:18px 20px; box-shadow:0 12px 40px rgba(0,0,0,.6);
           user-select:text; }
  .modal h3 { margin:0 0 10px; color:#f85149; font-size:15px; }
  .modal .eline { font-family:ui-monospace,Menlo,Consolas,monospace; color:#c9d1d9;
                  word-break:break-word; padding:2px 0; font-size:12px; line-height:1.5;
                  user-select:text; }
  .modal .cta { margin-top:14px; }
  .modal .cta a { color:#58a6ff; text-decoration:none; font-weight:600; }
  .modal .cta a:hover { text-decoration:underline; }
  .modal .closebar { display:flex; justify-content:flex-end; margin-top:14px; }
  .modal .closebar button { background:var(--dim); color:var(--fg); border:1px solid var(--line);
           border-radius:6px; padding:6px 14px; cursor:pointer; }
  /* multi-turn transcript */
  .turn { border-top:1px solid var(--line); padding-top:14px; margin-top:14px; }
  .turn:first-child { border-top:none; padding-top:0; margin-top:0; }
  .usermsg { background:var(--dim); border:1px solid var(--line); border-radius:8px;
             padding:8px 12px; margin-bottom:10px; }
  .usermsg .who { color:var(--accent); font-weight:600; font-size:12px; margin-bottom:2px; }
  .muted { color:var(--muted); }
  .spin { color:var(--accent); }
  .err { color:#f85149; }
  code { background:var(--dim); padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>JEV Dynamic Agent Orchestrator</h1>
  <p>Each prompt &rarr; JEV composes an agent (model + MCP servers + skills) from Unity Catalog metadata &rarr; the agent runs. Multi-turn: prompts append to the thread.</p>
</header>
<div class="wrap">
  <div class="left">
    <label>Prompt</label>
    <textarea id="prompt" placeholder="e.g. Analyze last quarter's revenue table and flag anomalies. Or: search the web for the latest on X and summarize."></textarea>

    <label>Selection threshold <span class="slider-val" id="thVal">0.40</span></label>
    <input type="range" id="th" min="0" max="1" step="0.05" value="0.4" style="width:100%"/>
    <div class="hint">MCP servers &amp; skills scoring at/above this JEV <code>noul</code> get selected.</div>

    <div class="row" style="margin-top:16px">
      <button class="btn" id="composeBtn" onclick="go(false)">Compose only</button>
      <button class="btn primary" id="runBtn" onclick="go(true)">Compose &amp; run agent</button>
      <button class="btn" id="clearBtn" onclick="clearChat()">Clear chat</button>
    </div>
    <div class="hint" id="status"></div>

    <label style="margin-top:22px">Discovered catalog</label>
    <div class="hint" id="catalogSummary">loading&hellip;</div>
  </div>

  <div class="right" id="out">
    <div class="muted">Enter a prompt and click <b>Compose</b> to see how JEV assembles an agent, or <b>Compose &amp; run agent</b> to also execute it. Each turn is appended below.</div>
  </div>
</div>

<div class="modal-back" id="modalBack" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modalBox"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const th = $("#th"); const thVal = $("#thVal");
th.oninput = () => thVal.textContent = (+th.value).toFixed(2);

async function loadCatalog(){
  try {
    const r = await fetch("/orch/catalog");
    const c = await r.json();
    const nm = (c.models||[]).length, nc = (c.mcp_servers||[]).length, ns = (c.skills||[]).length;
    $("#catalogSummary").innerHTML =
      `<b>${nm}</b> models &middot; <b>${nc}</b> MCP servers &middot; <b>${ns}</b> skills`
      + (c.error ? `<div class="err">${c.error}</div>` : "");
  } catch(e){ $("#catalogSummary").innerHTML = `<span class="err">${e}</span>`; }
}
loadCatalog();

function pct(x){ return (100*(x||0)).toFixed(0) + "%"; }

function renderItems(title, items){
  if(!items || !items.length) return "";
  let h = `<div class="card"><h2>${title}</h2>`;
  items.sort((a,b)=> (b.noul||0)-(a.noul||0));
  for(const it of items){
    const sel = it.selected;
    h += `<div class="item">
      <div class="top">
        <span class="name ${sel?'sel':'off'}">${sel?'✓ ':''}${it.label}</span>
        <span class="pct">${pct(it.noul)}</span>
      </div>
      <div class="desc">${(it.description||'').replace(/</g,'&lt;')}</div>
      <div class="bar ${sel?'sel':''}"><span style="width:${pct(it.noul)}"></span></div>
    </div>`;
  }
  return h + `</div>`;
}

function renderSpec(spec){
  const m = spec.model || {};
  // JEV returns a probability per candidate model label; show the top 3 so the
  // choice is legible, with the winner highlighted.
  const probs = m.probabilities || {};
  const ranked = Object.keys(probs)
    .map(k => ({label:k, p:Number(probs[k])||0}))
    .sort((a,b) => b.p - a.p)
    .slice(0,3);
  let cands = "";
  if(ranked.length){
    cands = `<div class="cands">` + ranked.map((r,i) => {
      const win = (r.label===m.label);
      return `<div class="cand${win?' win':''}">`
        + `<span class="rank">${i+1}</span>`
        + `<span class="clabel">${escHtml(r.label)}${win?' &check;':''}</span>`
        + `<span class="cbar"><span style="width:${(r.p*100).toFixed(1)}%"></span></span>`
        + `<span class="cpct">${pct(r.p)}</span></div>`;
    }).join("") + `</div>`;
  }
  let h = `<div class="card"><h2>Model &mdash; JEV choice</h2>
    <div><span class="model-pick">${m.model||'(none)'}</span>
    <span class="conf">confidence ${pct(m.confidence)} &middot; JEV ${spec.latency_ms} ms</span></div>
    ${cands}</div>`;
  h += renderItems("MCP servers (noul &ge; "+(spec.threshold).toFixed(2)+")", spec.mcp_servers);
  h += renderItems("Skills (noul &ge; "+(spec.threshold).toFixed(2)+")", spec.skills);
  return h;
}

function renderRun(run){
  if(!run) return "";
  if(run.error) return `<div class="card"><h2>Agent run</h2><div class="err">${run.error}</div></div>`;
  let tags = (run.skill_names||[]).map(s=>`<span class="tag good">skill: ${s}</span>`).join("")
           + (run.tools_used||[]).map(t=>`<span class="tag">tool: ${t}</span>`).join("");
  let mcp = "";
  for(const r of (run.mcp_report||[])){
    if(r.label==='(model fallback)'){
      mcp += `<span class="tag" style="border-color:var(--accent)" title="${escHtml(r.error||'')}">model fallback</span>`;
      continue;
    }
    if(r.loaded){
      mcp += `<span class="tag good">${r.label}: ${ (r.tools||[]).length } tools</span>`;
      continue;
    }
    // Scan the WHOLE error + error_chain so nested ExceptionGroup/TaskGroup
    // wrappers don't hide the real cause (e.g. McpError UNAUTHENTICATED).
    const blob = [(r.error||''), ...((r.error_chain)||[])].join(' \n ');
    let reason = 'load error';
    if(!r.error){ reason = 'no tools'; }
    else if(/server cap/i.test(blob)){ reason = 'skipped (server cap)'; }
    else if(/UNAUTHENTICATED|not found for the connection|login first|401|403|Forbidden/i.test(blob)){
      reason = 'not authorized \u2014 log in to this connection'; }
    else if(/timeout|timed out/i.test(blob)){ reason = 'timed out'; }
    // Stash full detail so a click can open it in a selectable modal.
    let lines = (Array.isArray(r.error_chain) && r.error_chain.length)
                ? r.error_chain : [r.error||''];
    const idx = ERRSTORE.push({label:r.label, reason, lines}) - 1;
    mcp += `<span class="tag bad clickable" onclick="openErr(${idx})" title="click for details">${r.label}: ${reason}</span>`;
  }
  const jevM = run.model||'';
  const ranM = run.run_model||jevM;
  const hdr = (ranM && ranM!==jevM)
    ? `${jevM} <span style="opacity:.7">&rarr; fell back to</span> ${ranM}`
    : jevM;
  return `<div class="card"><h2>Composed agent &mdash; reply from ${hdr}</h2>
    <div class="reply">${(run.reply||'(empty)').replace(/</g,'&lt;')}</div>
    <div style="margin-top:10px">${tags}${mcp}</div></div>`;
}

// ---- error modal ----
const ERRSTORE = [];
function escHtml(s){ return (s||'').toString()
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function openErr(i){
  const e = ERRSTORE[i]; if(!e) return;
  const full = e.lines.join('\n');
  const m = full.match(/https?:\/\/[^\s"'}]+/);
  const loginUrl = m ? m[0] : null;
  const body = e.lines.map(l => `<div class="eline">${escHtml(l)}</div>`).join('');
  const cta = loginUrl
    ? `<div class="cta"><a href="${escHtml(loginUrl)}" target="_blank" rel="noopener">\u2192 Authorize this connection</a></div>` : '';
  $("#modalBox").innerHTML =
    `<h3>${escHtml(e.label)} \u2014 ${escHtml(e.reason)}</h3>${body}${cta}`
    + `<div class="closebar"><button onclick="closeModal()">Close</button></div>`;
  $("#modalBack").classList.add('open');
}
function closeModal(){ $("#modalBack").classList.remove('open'); }
document.addEventListener('keydown', e => { if(e.key==='Escape') closeModal(); });

// ---- multi-turn chat state ----
// MESSAGES is the running thread sent to the engine every turn; TURNS holds the
// rendered HTML for each exchange so the transcript accumulates on screen.
let MESSAGES = [];
let TURNS = [];

function renderTranscript(){
  if(!TURNS.length){
    $("#out").innerHTML = '<div class="muted">Enter a prompt and click <b>Compose</b> or <b>Compose &amp; run agent</b>. Each turn is appended below.</div>';
    return;
  }
  $("#out").innerHTML = TURNS.map(t => `<div class="turn">${t}</div>`).join("");
  $("#out").scrollTop = $("#out").scrollHeight;
}

function clearChat(){
  MESSAGES = []; TURNS = []; ERRSTORE.length = 0;
  $("#prompt").value = "";
  renderTranscript();
  $("#status").innerHTML = '';
}

async function go(runIt){
  const prompt = $("#prompt").value.trim();
  if(!prompt){ $("#status").innerHTML = '<span class="err">Enter a prompt.</span>'; return; }
  $("#composeBtn").disabled = true; $("#runBtn").disabled = true; $("#clearBtn").disabled = true;
  $("#status").innerHTML = '<span class="spin">'+(runIt?'Composing + running agent&hellip;':'Composing&hellip;')+'</span>';
  // Append this user prompt to the running thread (multi-turn).
  MESSAGES.push({role:"user", content:prompt});
  const userBlock = `<div class="usermsg"><div class="who">you</div>${escHtml(prompt)}</div>`;
  const body = { messages: MESSAGES, threshold:+th.value };
  try {
    const url = runIt ? "/orch/run" : "/orch/compose";
    const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    const data = await r.json();
    if(data.error && !data.spec){
      TURNS.push(userBlock + `<div class="err">${escHtml(data.error)}</div>`);
    } else {
      const spec = runIt ? data.spec : data;
      const html = renderSpec(spec) + (runIt ? renderRun(data.run) : "");
      TURNS.push(userBlock + html);
      // Feed the assistant reply back into the thread so the next turn has context.
      if(runIt && data.run && data.run.reply){
        MESSAGES.push({role:"assistant", content:data.run.reply});
      }
    }
    renderTranscript();
    $("#prompt").value = "";
    $("#status").innerHTML = '';
  } catch(e){
    // roll back the just-added user message so a retry doesn't double it
    MESSAGES.pop();
    $("#status").innerHTML = `<span class="err">${e}</span>`;
  } finally {
    $("#composeBtn").disabled = false; $("#runBtn").disabled = false; $("#clearBtn").disabled = false;
  }
}
</script>
</body>
</html>
"""
