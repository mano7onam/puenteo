"""Single-file dashboard served at ``/`` by ``puenteo serve`` (no build step, no CDN)."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>puenteo</title>
<style>
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e4df;--accent:#2f6fde;--ok:#1f8a4c;--warn:#b26b00;--chip:#eef2fb}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--mute:#9a9992;--line:#2e2e2b;--accent:#7aa7ff;--ok:#5cc489;--warn:#e5a54a;--chip:#25304a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:2}
header b{font-size:16px}header .st{margin-left:auto;color:var(--mute);font-size:12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mute);display:inline-block;margin-right:6px}.dot.on{background:var(--ok)}
main{display:grid;grid-template-columns:minmax(280px,380px) 1fr;gap:16px;padding:16px 20px;max-width:1400px;margin:0 auto}
@media (max-width:860px){main{grid-template-columns:1fr;padding:12px 16px}}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
h2{margin:0;padding:10px 14px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);border-bottom:1px solid var(--line)}
.list{max-height:46vh;overflow:auto}.row{padding:9px 14px;border-bottom:1px solid var(--line);cursor:pointer}.row:hover{background:var(--chip)}
.row .a{font:12px ui-monospace,Menlo,monospace;color:var(--accent)}.row .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .c{color:var(--mute);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{font-size:11px;padding:1px 6px;border-radius:9px;background:var(--chip);margin-left:6px}.busy{color:var(--warn)}
#feed{height:58vh;overflow:auto;padding:6px 0}.msg{padding:8px 14px;border-bottom:1px solid var(--line)}
.msg .h{font:12px ui-monospace,Menlo,monospace;color:var(--mute)}.msg .h b{color:var(--accent);font-weight:600}
.msg .b{white-space:pre-wrap;word-break:break-word;margin-top:3px}
form{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--line);flex-wrap:wrap}
input,textarea,button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:7px 10px}
#to{flex:0 0 260px;max-width:100%}#text{flex:1 1 300px;min-height:38px;resize:vertical}button{background:var(--accent);color:#fff;border:0;cursor:pointer}
.search{display:flex;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line)}.search input{flex:1}
.hit{padding:8px 14px;border-bottom:1px solid var(--line)}.hit .s{color:var(--mute);font-size:12px}
.empty{padding:14px;color:var(--mute)}
</style></head><body>
<header><b>puenteo</b><span style="color:var(--mute)">bridge between agent sessions</span>
<span class="st"><span id="live" class="dot"></span><span id="stat">connecting…</span></span></header>
<main>
<div style="display:grid;gap:16px;align-content:start">
 <section><h2>Live sessions</h2><div id="ps" class="list"><div class="empty">loading…</div></div></section>
 <section><h2>Claims</h2><div id="claims" class="list"><div class="empty">none</div></div></section>
 <section><h2>Other machines <span id="meshstate" style="text-transform:none;letter-spacing:0;font-weight:400"></span></h2><div id="mesh" class="list"><div class="empty">loading…</div></div></section>
 <section><h2>Bazaar offers</h2><div id="offers" class="list"><div class="empty">none</div></div></section>
</div>
<div style="display:grid;gap:16px;align-content:start">
 <section><h2>Bus traffic</h2><div id="feed"></div>
  <form id="f"><input id="to" placeholder="to: claude:… · @name · #channel · cwd:<path> · *" required>
  <textarea id="text" placeholder="message" required></textarea><button>Send</button></form></section>
 <section><h2>Search every agent's history</h2><div class="search"><input id="q" placeholder="what did we decide about …"><button id="go">Search</button></div><div id="hits"></div></section>
</div></main>
<script>
const T=new URLSearchParams(location.search).get('token')||'';
const api=(p,o={})=>fetch(p+(p.includes('?')?'&':'?')+'token='+encodeURIComponent(T),{...o,headers:{'Content-Type':'application/json',...(o.headers||{})}}).then(async r=>{const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j});
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ago=t=>{if(!t)return'';const s=Date.now()/1e3-(typeof t==='number'?t:Date.parse(t)/1e3);return s<90?Math.round(s)+'s':s<5400?Math.round(s/60)+'m':s<172800?Math.round(s/3600)+'h':Math.round(s/86400)+'d'};
async function ps(){try{const r=await api('/api/ps');document.getElementById('ps').innerHTML=r.length?r.map(s=>`<div class="row" data-a="${esc(s.address)}"><div class="t">${esc(s.name||'(unnamed)')}${s.status==='busy'?'<span class="tag busy">busy</span>':''}${s.is_me?'<span class="tag">you</span>':''}</div><div class="a">${esc(s.address)}</div><div class="c">${esc(s.cwd)} · ${esc(s.last_active)}</div></div>`).join(''):'<div class="empty">no live sessions</div>';
 document.querySelectorAll('#ps .row').forEach(e=>e.onclick=()=>{document.getElementById('to').value=e.dataset.a;document.getElementById('text').focus()})}catch(e){}}
async function claims(){try{const r=await api('/api/claims');document.getElementById('claims').innerHTML=r.length?r.map(c=>`<div class="row"><div class="t">${esc(c.resource)}</div><div class="c">${esc(c.holder)} · ${Math.round(c.ttl_left_s/60)} min left ${esc(c.note)}</div></div>`).join(''):'<div class="empty">none</div>'}catch(e){}}
const feed=document.getElementById('feed');
function add(m){const d=document.createElement('div');d.className='msg';d.innerHTML=`<div class="h"><b>${esc(m.sender)}</b> → ${esc(m.to)} · ${esc((m.created_iso||'').slice(11))} · ${esc(m.id)}</div><div class="b">${esc(m.body)}</div>`;feed.appendChild(d);feed.scrollTop=feed.scrollHeight}
api('/api/log?limit=50').then(ms=>ms.forEach(add)).finally(()=>{if(new URLSearchParams(location.search).has('nosse')){live.className='dot on';stat.textContent='live';return}const es=new EventSource('/api/events?token='+encodeURIComponent(T));
 es.onopen=()=>{live.className='dot on';stat.textContent='live'};es.onerror=()=>{live.className='dot';stat.textContent='reconnecting…'};
 es.addEventListener('message',e=>{add(JSON.parse(e.data));claims()})});
document.getElementById('f').onsubmit=async e=>{e.preventDefault();try{await api('/api/send',{method:'POST',body:JSON.stringify({to:to.value,text:text.value})});text.value=''}catch(err){alert(err.message)}};
async function search(){const q=document.getElementById('q').value.trim();if(!q)return;const h=document.getElementById('hits');h.innerHTML='<div class="empty">searching…</div>';
 try{const r=await api('/api/search?q='+encodeURIComponent(q));h.innerHTML=r.length?r.map(x=>`<div class="hit"><div class="a" style="font:12px ui-monospace,monospace;color:var(--accent)">${esc(x.session)} #${x.msg} ${esc(x.role)}</div><div>${esc(x.snippet)}</div><div class="s">${esc(x.title)} · ${esc(x.cwd)}</div></div>`).join(''):'<div class="empty">no hits</div>'}catch(e){h.innerHTML='<div class="empty">'+esc(e.message)+'</div>'}}
document.getElementById('go').onclick=search;document.getElementById('q').onkeydown=e=>{if(e.key==='Enter')search()};
async function mesh(){try{const r=await api('/api/mesh');const p=r.peers||{};
 meshstate.textContent=p.bridge_running?`· ${esc(p.this_node)} · bridge on`:'· bridge off (puenteo mesh service install)';
 const rows=(p.nodes||[]).map(n=>`<div class="row"><div class="t">${esc(n.node)}${n.trusted?'<span class="tag">trusted</span>':''}</div><div class="c">${n.sessions.length} session(s) · seen ${n.seen_s_ago}s ago</div>${n.sessions.slice(0,4).map(s=>`<div class="a" data-a="${esc(s)}">${esc(s)}</div>`).join('')}</div>`);
 document.getElementById('mesh').innerHTML=rows.length?rows.join(''):'<div class="empty">no other machines online</div>';
 document.getElementById('offers').innerHTML=(r.offers||[]).length?r.offers.map(o=>`<div class="row" data-a="${esc(o.address)}"><div class="t">${esc(o.text)}</div><div class="a">${esc(o.address)}</div><div class="c">${esc((o.tags||[]).join(', '))}</div></div>`).join(''):'<div class="empty">no offers (publish one: puenteo offer "…")</div>';
 document.querySelectorAll('#mesh [data-a], #offers [data-a]').forEach(e=>e.onclick=()=>{to.value=e.dataset.a;text.focus()})}catch(e){}}
ps();claims();mesh();setInterval(ps,5000);setInterval(claims,15000);setInterval(mesh,20000);
</script></body></html>
"""
