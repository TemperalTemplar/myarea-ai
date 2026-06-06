"""
Approval API — Piece 4. The human-in-the-loop control surface.

ETHICAL DESIGN:
  - This entire blueprint is CSSHI-only (Alva / platform owner). Nobody else can
    view incoming mail, request drafts, or approve sends.
  - "Draft a reply" is the ONLY trigger that runs the model (on-demand, thermal-gated).
  - "Approve" is what authorizes a send. Replies requiring approval cannot send
    without an explicit approve action here.

Routes (all CSSHI-only):
  GET  /approvals                      — the approval web UI (HTML)
  GET  /api/approvals/incoming         — recent incoming emails (from journal, email-in)
  POST /api/approvals/draft            — on-demand: draft a reply for an email {from,subject,original}
  GET  /api/approvals/replies          — list reply drafts (optionally by status)
  POST /api/approvals/<rid>/approve    — approve a draft (queues it to send)
  POST /api/approvals/<rid>/reject     — reject a draft
"""
import logging
from flask import Blueprint, request, jsonify, Response

from ..auth.tiers import resolve_tier, _extract_bearer
from ..replies.store import (
    draft_reply_for, list_replies, get_reply, set_status,
    REPLY_APPROVED, REPLY_REJECTED,
)

logger = logging.getLogger(__name__)
approvals_bp = Blueprint("approvals", __name__)


def _csshi_guard():
    """CSSHI tier, or SERVICE_API_KEY equivalent. Returns None if allowed."""
    from flask import current_app
    tier = resolve_tier(request)
    if tier == "csshi":
        return None
    token = _extract_bearer(request)
    if token and token == current_app.config.get("SERVICE_API_KEY", ""):
        return None
    return jsonify({"error": "csshi tier required"}), 403


def _redis():
    from ..extensions import redis_client
    return redis_client


_HANDLED_KEY = "silex:email:handled_jids"


@approvals_bp.get("/api/approvals/incoming")
def incoming():
    g = _csshi_guard()
    if g: return g
    r = _redis()
    # subjects that already have a reply (sent/pending/approved) — auto-replies included
    replied_subjects = set()
    try:
        for rep in list_replies(limit=200):
            subj = (rep.get("subject") or "").strip().lower()
            # normalize "re: x" and "x" to the same key
            replied_subjects.add(subj[4:].strip() if subj.startswith("re:") else subj)
    except Exception:
        pass

    out = []
    for jid in r.lrange("silex:journal:entries", 0, 80):
        if r.sismember(_HANDLED_KEY, jid):
            continue  # already replied to / dismissed
        h = r.hgetall(f"silex:journal:entry:{jid}")
        if h.get("source") != "email-in":
            continue
        content = h.get("content", "")
        # derive subject from the content to compare against replied set
        subj = ""
        for line in content.split("\n"):
            if line.lower().startswith("subject:"):
                subj = line.split(":", 1)[1].strip().lower()
                break
        norm = subj[4:].strip() if subj.startswith("re:") else subj
        if norm and norm in replied_subjects:
            continue  # an auto-reply or manual reply already went out for this
        out.append({"id": jid, "content": content, "ts": h.get("timestamp", "")})
    return jsonify({"incoming": out})


@approvals_bp.post("/api/approvals/dismiss")
def dismiss():
    """Mark an incoming email as handled without replying (hide it from the list)."""
    g = _csshi_guard()
    if g: return g
    data = request.get_json(silent=True) or {}
    jid = (data.get("id") or "").strip()
    if not jid:
        return jsonify({"ok": False, "error": "id required"}), 400
    _redis().sadd(_HANDLED_KEY, jid)
    return jsonify({"ok": True})


@approvals_bp.post("/api/approvals/draft")
def draft():
    g = _csshi_guard()
    if g: return g
    data = request.get_json(silent=True) or {}
    from_header = (data.get("from") or "").strip()
    subject     = (data.get("subject") or "").strip()
    original    = (data.get("original") or "").strip()
    jid         = (data.get("id") or "").strip()
    if not from_header or not original:
        return jsonify({"ok": False, "error": "from and original are required"}), 400
    result = draft_reply_for(from_header, subject, original)
    # mark this incoming email handled so it leaves the incoming list
    if result.get("ok") and jid:
        _redis().sadd(_HANDLED_KEY, jid)
    return jsonify(result)


@approvals_bp.get("/api/approvals/replies")
def replies():
    g = _csshi_guard()
    if g: return g
    status = request.args.get("status")
    return jsonify({"replies": list_replies(status=status)})


@approvals_bp.post("/api/approvals/<rid>/approve")
def approve(rid):
    g = _csshi_guard()
    if g: return g
    entry = get_reply(rid)
    if not entry:
        return jsonify({"ok": False, "error": "not found"}), 404
    # Allow an edited body to be supplied at approval time
    data = request.get_json(silent=True) or {}
    edited = data.get("draft")
    if edited:
        _redis().hset(f"silex:replies:entry:{rid}", "draft", edited)
    set_status(rid, REPLY_APPROVED)
    logger.info("reply approved: %s -> %s", rid, entry.get("to"))
    # Send happens via the send path (piece 5); for now mark approved.
    try:
        from ..replies.send import send_approved_reply
        send_result = send_approved_reply(rid)
        return jsonify({"ok": True, "approved": True, "send": send_result})
    except Exception as exc:
        logger.warning("send path not available yet: %s", exc)
        return jsonify({"ok": True, "approved": True, "send": {"deferred": True}})


@approvals_bp.post("/api/approvals/<rid>/reject")
def reject(rid):
    g = _csshi_guard()
    if g: return g
    if not get_reply(rid):
        return jsonify({"ok": False, "error": "not found"}), 404
    set_status(rid, REPLY_REJECTED)
    return jsonify({"ok": True, "rejected": True})


@approvals_bp.get("/approvals")
def approvals_page():
    # Page is SSO-gated; its API fetches send the CSSHI token (entered + stored
    # in the browser, same pattern as the journal/security pages).
    from ..auth.sso import login_required as _lr
    # apply login_required behavior inline
    from flask import session, redirect
    return _gated_page()


def _gated_page():
    from ..auth.sso import get_current_user
    user = None
    try:
        user = get_current_user()
    except Exception:
        user = None
    if not user:
        from flask import redirect
        return redirect("/auth/login")
    return Response(_PAGE_HTML, mimetype="text/html")


_PAGE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Approvals — Silex</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root{--bg:#0d0f14;--bg2:#111520;--bg3:#161b28;--border:#1e2535;--border2:#2a3448;
        --accent:#e63946;--accent2:#ff4d5a;--green:#00d4a0;--text:#e8eaf0;--text-dim:#8899bb;
        --text-muted:#4a5a7a;--mono:'Space Mono',monospace;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:var(--mono);padding:20px;max-width:900px;margin:0 auto;}
  h1{font-family:'Archivo Black',sans-serif;font-size:20px;margin-bottom:4px;}
  .logo{font-size:12px;letter-spacing:3px;color:var(--accent);font-weight:700;}
  .logo span{color:var(--text-dim);}
  h2{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--text-muted);
     margin:24px 0 10px;border-bottom:1px solid var(--border);padding-bottom:6px;}
  .card{background:var(--bg2);border:1px solid var(--border2);border-radius:8px;padding:14px;margin-bottom:10px;}
  .meta{font-size:11px;color:var(--text-dim);margin-bottom:8px;white-space:pre-wrap;line-height:1.5;}
  .draft{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;
         font-size:13px;line-height:1.6;white-space:pre-wrap;color:var(--text);width:100%;min-height:80px;
         font-family:var(--mono);resize:vertical;}
  .row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;}
  button{background:var(--bg3);border:1px solid var(--border2);color:var(--text-dim);
         border-radius:6px;padding:8px 14px;font-family:var(--mono);font-size:11px;
         letter-spacing:0.08em;text-transform:uppercase;cursor:pointer;transition:all .15s;}
  button:hover{border-color:var(--accent);color:var(--text);}
  button.go{background:var(--green);border-color:var(--green);color:#04241c;font-weight:700;}
  button.go:hover{background:#00f0b6;}
  button.warn{border-color:var(--accent);color:var(--accent2);}
  .pill{display:inline-block;font-size:9px;letter-spacing:0.1em;text-transform:uppercase;
        padding:2px 7px;border-radius:4px;border:1px solid var(--border2);color:var(--text-muted);}
  .pill.approval{border-color:var(--accent);color:var(--accent2);}
  .pill.alva{border-color:var(--green);color:var(--green);}
  .msg{font-size:12px;margin-top:8px;}
  .empty{color:var(--text-muted);font-size:12px;padding:10px 0;}
</style></head><body>
  <div class="logo">MY<span>AREA</span> · SILEX</div>
  <h1>Reply Approvals</h1>

  <h2>Incoming mail — request a draft to reply</h2>
  <div id="incoming"><div class="empty">Loading…</div></div>

  <h2>Reply drafts — review &amp; approve</h2>
  <div id="drafts"><div class="empty">Loading…</div></div>

<script>
var csshiToken = localStorage.getItem('silex_csshi_token') || '';
if(!csshiToken){
  csshiToken = prompt('Enter CSSHI token to manage approvals:') || '';
  if(csshiToken) localStorage.setItem('silex_csshi_token', csshiToken);
}
function authHeaders(extra){
  var h = {'Authorization':'Bearer '+csshiToken, 'X-Silex-Tier':'csshi'};
  if(extra) for(var k in extra) h[k]=extra[k];
  return h;
}
async function jget(u){ var r=await fetch(u,{headers:authHeaders()}); if(r.status===403){ localStorage.removeItem('silex_csshi_token'); alert('CSSHI token invalid — reload to re-enter.'); } return r.json(); }
async function jpost(u,b){ var r=await fetch(u,{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(b||{})}); return r.json(); }

function esc(s){ return (s||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

async function loadIncoming(){
  var box=document.getElementById('incoming');
  try{
    var d=await jget('/api/approvals/incoming');
    if(!d.incoming||!d.incoming.length){ box.innerHTML='<div class="empty">No incoming mail recorded.</div>'; return; }
    box.innerHTML='';
    d.incoming.forEach(function(m){
      var c=document.createElement('div'); c.className='card';
      c.innerHTML='<div class="meta">'+esc(m.content)+'</div>';
      var row=document.createElement('div'); row.className='row';
      var b=document.createElement('button'); b.className='go'; b.textContent='Draft a reply';
      b.addEventListener('click',async function(){
        b.disabled=true; b.textContent='Drafting…';
        // parse from/subject out of the journal content lines
        var lines=m.content.split('\\n');
        var from='', subj='', orig=m.content;
        lines.forEach(function(l){
          if(l.toLowerCase().indexOf('from')>=0 && !from) from=l.replace(/^[^:]*:/,'').trim();
          if(l.toLowerCase().indexOf('subject')>=0 && !subj) subj=l.replace(/^[^:]*:/,'').trim();
        });
        var res=await jpost('/api/approvals/draft',{from:from,subject:subj,original:orig,id:m.id});
        if(res.ok){ b.textContent='Drafted ✓'; setTimeout(function(){loadIncoming();loadDrafts();},600); }
        else if(res.error==='thermal_gate'){ b.disabled=false; b.textContent='Too hot — retry'; alert(res.detail||'GPU too hot'); }
        else { b.disabled=false; b.textContent='Draft a reply'; alert(res.error||'Draft failed'); }
      });
      var dis=document.createElement('button'); dis.textContent='Dismiss';
      dis.addEventListener('click',async function(){
        await jpost('/api/approvals/dismiss',{id:m.id});
        loadIncoming();
      });
      row.appendChild(b); row.appendChild(dis); c.appendChild(row); box.appendChild(c);
    });
  }catch(e){ box.innerHTML='<div class="empty">Error loading incoming.</div>'; }
}

async function loadDrafts(){
  var box=document.getElementById('drafts');
  try{
    var d=await jget('/api/approvals/replies?status=pending');
    if(!d.replies||!d.replies.length){ box.innerHTML='<div class="empty">No pending drafts.</div>'; return; }
    box.innerHTML='';
    d.replies.forEach(function(rep){
      var c=document.createElement('div'); c.className='card';
      var needsApproval=rep.requires_approval==='1';
      var pill=needsApproval?'<span class="pill approval">approval required</span>':'<span class="pill alva">to Alva</span>';
      c.innerHTML='<div class="meta">'+pill+'  To: '+esc(rep.to)+'\\nSubject: '+esc(rep.subject)+'</div>';
      var ta=document.createElement('textarea'); ta.className='draft'; ta.value=rep.draft;
      c.appendChild(ta);
      var row=document.createElement('div'); row.className='row';
      var approve=document.createElement('button'); approve.className='go'; approve.textContent='Approve & Send';
      approve.addEventListener('click',async function(){
        if(!confirm('Send this reply to '+rep.to+'?'))return;
        approve.disabled=true; approve.textContent='Sending…';
        var res=await jpost('/api/approvals/'+rep.id+'/approve',{draft:ta.value});
        if(res.ok){ c.style.opacity=0.4; approve.textContent='Sent ✓'; setTimeout(loadDrafts,800); }
        else { approve.disabled=false; approve.textContent='Approve & Send'; alert(res.error||'Failed'); }
      });
      var reject=document.createElement('button'); reject.className='warn'; reject.textContent='Reject';
      reject.addEventListener('click',async function(){
        if(!confirm('Reject and discard this draft?'))return;
        await jpost('/api/approvals/'+rep.id+'/reject',{});
        loadDrafts();
      });
      row.appendChild(approve); row.appendChild(reject); c.appendChild(row); box.appendChild(c);
    });
  }catch(e){ box.innerHTML='<div class="empty">Error loading drafts.</div>'; }
}

loadIncoming(); loadDrafts();
setInterval(function(){ loadIncoming(); loadDrafts(); }, 20000);
</script>
</body></html>"""
