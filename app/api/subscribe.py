"""
Subscriber signup + unsubscribe endpoints — public-facing, consent-managed.

ETHICAL DESIGN:
  - /subscribe is the ONLY way onto the outbound list; explicit consent recorded.
  - Subscribing is OUTBOUND-ONLY: it lets Silex (after approval) write to you;
    it does NOT let you inject into her inbox/context.
  - /unsubscribe/<token> is one-click, immediate, permanent, no login required
    (the token IS the auth — it's unguessable and unique per subscriber).
  - Basic rate-limiting + input validation guard the public surface.

Routes:
  GET  /subscribe                  — the signup page (HTML)
  POST /api/subscribe              — submit signup (JSON or form)
  GET  /unsubscribe/<token>        — one-click unsubscribe (HTML confirmation)
"""
import re
import logging
import time
from flask import Blueprint, request, jsonify, Response

from ..subscribers.store import add_subscriber, unsubscribe_by_token

logger = logging.getLogger(__name__)
subscribe_bp = Blueprint("subscribe", __name__)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

CONSENT_TEXT = ("I consent to receive email correspondence from Silex (the MyArea AI). "
                "I understand I can unsubscribe at any time via the link in any message.")

# crude in-memory rate limit: ip -> last submit ts
_LAST_SUBMIT = {}
_RATE_SECONDS = 10


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


@subscribe_bp.get("/subscribe")
def signup_page():
    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Subscribe — Silex | MyArea</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root{--bg:#0d0f14;--bg2:#111520;--bg3:#161b28;--border:#1e2535;--border2:#2a3448;
        --accent:#e63946;--accent2:#ff4d5a;--text:#e8eaf0;--text-dim:#8899bb;--text-muted:#4a5a7a;
        --mono:'Space Mono',monospace;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:20px;}
  .card{background:var(--bg2);border:1px solid var(--border2);border-radius:12px;
        max-width:440px;width:100%;padding:32px;}
  .logo{font-size:14px;letter-spacing:3px;color:var(--accent);font-weight:700;margin-bottom:4px;}
  .logo span{color:var(--text-dim);}
  h1{font-family:'Archivo Black',sans-serif;font-size:22px;margin:16px 0 8px;letter-spacing:0.02em;}
  p.sub{color:var(--text-dim);font-size:13px;line-height:1.6;margin-bottom:24px;}
  label{display:block;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;
        color:var(--text-muted);margin:14px 0 6px;}
  input{width:100%;background:var(--bg3);border:1px solid var(--border2);border-radius:6px;
        color:var(--text);font-family:var(--mono);font-size:14px;padding:11px 13px;outline:none;}
  input:focus{border-color:var(--accent);}
  .consent{display:flex;gap:10px;align-items:flex-start;margin:18px 0;font-size:12px;
           color:var(--text-dim);line-height:1.5;}
  .consent input{width:auto;margin-top:3px;flex-shrink:0;}
  button{width:100%;background:var(--accent);color:#fff;border:none;border-radius:6px;
         font-family:var(--mono);font-size:13px;letter-spacing:0.1em;text-transform:uppercase;
         padding:13px;cursor:pointer;margin-top:8px;transition:background .15s;}
  button:hover:not(:disabled){background:var(--accent2);}
  button:disabled{background:var(--text-muted);cursor:not-allowed;}
  .msg{margin-top:16px;font-size:13px;padding:11px;border-radius:6px;display:none;}
  .msg.ok{display:block;background:rgba(0,212,160,.1);border:1px solid #00d4a0;color:#00d4a0;}
  .msg.err{display:block;background:rgba(230,57,70,.1);border:1px solid var(--accent);color:var(--accent2);}
  .foot{margin-top:20px;font-size:10px;color:var(--text-muted);line-height:1.5;}
</style></head>
<body>
  <div class="card">
    <div class="logo">MY<span>AREA</span></div>
    <h1>Correspond with Silex</h1>
    <p class="sub">Sign up to receive email correspondence from Silex, the MyArea AI.
       Every message includes a one-click unsubscribe link. Your address is used only
       for this correspondence and is never shared.</p>
    <label for="name">Name (optional)</label>
    <input id="name" type="text" placeholder="Your name" autocomplete="name">
    <label for="email">Email address</label>
    <input id="email" type="email" placeholder="you@example.com" autocomplete="email" required>
    <div class="consent">
      <input id="consent" type="checkbox">
      <span>I consent to receive email correspondence from Silex and understand I can
            unsubscribe at any time.</span>
    </div>
    <button id="submit" disabled>Subscribe</button>
    <div id="msg" class="msg"></div>
    <div class="foot">MyArea is a sovereign, self-hosted platform. This list is consent-based;
       you control your subscription and can leave instantly via any message footer.</div>
  </div>
<script>
  var consent=document.getElementById('consent'), btn=document.getElementById('submit'),
      email=document.getElementById('email'), nameEl=document.getElementById('name'),
      msg=document.getElementById('msg');
  consent.addEventListener('change',function(){ btn.disabled=!consent.checked; });
  btn.addEventListener('click',async function(){
    msg.className='msg';
    var e=email.value.trim();
    if(!e||e.indexOf('@')<0){ msg.className='msg err'; msg.textContent='Please enter a valid email.'; return; }
    btn.disabled=true; btn.textContent='Subscribing…';
    try{
      var r=await fetch('/api/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:e,name:nameEl.value.trim(),consent:consent.checked})});
      var raw=await r.text();
      var d;
      try{ d=JSON.parse(raw); }
      catch(parseErr){
        msg.className='msg err';
        msg.textContent='Server returned non-JSON (HTTP '+r.status+'): '+raw.slice(0,120);
        btn.disabled=false; btn.textContent='Subscribe'; return;
      }
      if(d.ok){ msg.className='msg ok'; msg.textContent='You are subscribed. Welcome.'; btn.textContent='Subscribed'; }
      else { msg.className='msg err'; msg.textContent=d.error||'Something went wrong.'; btn.disabled=false; btn.textContent='Subscribe'; }
    }catch(err){ msg.className='msg err'; msg.textContent='Fetch failed: '+(err&&err.message?err.message:String(err)); btn.disabled=false; btn.textContent='Subscribe'; }
  });
</script>
</body></html>"""
    return Response(html, mimetype="text/html")


@subscribe_bp.post("/api/subscribe")
def do_subscribe():
    # rate limit
    ip = _client_ip()
    now = time.time()
    if ip in _LAST_SUBMIT and now - _LAST_SUBMIT[ip] < _RATE_SECONDS:
        return jsonify({"ok": False, "error": "Please wait a moment before trying again."}), 429
    _LAST_SUBMIT[ip] = now

    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip()
    name = (data.get("name") or "").strip()
    consent = data.get("consent")

    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Please enter a valid email address."}), 400
    if not consent:
        return jsonify({"ok": False, "error": "Consent is required to subscribe."}), 400

    result = add_subscriber(email, name=name, consent_text=CONSENT_TEXT, source="web-signup")
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "Could not subscribe.")}), 400

    logger.info("subscribe: %s (reactivated=%s)", email, result.get("reactivated"))
    return jsonify({"ok": True})


@subscribe_bp.get("/unsubscribe/<token>")
def do_unsubscribe(token: str):
    result = unsubscribe_by_token(token)
    if result.get("ok"):
        body = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Unsubscribed</title>
<style>body{{background:#0d0f14;color:#e8eaf0;font-family:'Space Mono',monospace;
display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:20px;}}
.box{{max-width:420px;}}.a{{color:#e63946;letter-spacing:3px;font-weight:700;}}</style></head>
<body><div class="box"><div class="a">MYAREA</div>
<h2 style="margin:16px 0;">You've been unsubscribed</h2>
<p style="color:#8899bb;line-height:1.6;">{result['email']} has been removed. Silex will no longer
email you. You can re-subscribe any time from the signup page.</p></div></body></html>"""
        return Response(body, mimetype="text/html")
    body = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Link invalid</title>
<style>body{background:#0d0f14;color:#e8eaf0;font-family:'Space Mono',monospace;
display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:20px;}</style></head>
<body><div><h2>This unsubscribe link is invalid or already used.</h2>
<p style="color:#8899bb;">If you believe this is an error, contact the platform owner.</p></div></body></html>"""
    return Response(body, mimetype="text/html", status=404)
