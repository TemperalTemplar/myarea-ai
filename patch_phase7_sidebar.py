#!/usr/bin/env python3
"""
Phase 7 patch — add a sessions/projects sidebar to index.html.
Surgical: wraps the existing #shell in a flex row with a sidebar beside it,
adds sidebar CSS, and injects the sessions/projects JS. Idempotent.
"""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "phase7-sidebar" in html:
    print("Phase 7 already applied — skipping")
    raise SystemExit(0)

# ── 1. CSS: sidebar + layout wrapper ───────────────────────────────────────────
sidebar_css = """
    /* ── Phase 7 sidebar ── */
    #workspace { display:flex; gap:0; height:calc(100vh - 52px); max-width:1200px; margin:0 auto; padding:20px 16px; }
    #sidebar { width:260px; flex-shrink:0; background:var(--bg2); border:1px solid var(--border); border-right:none; border-radius:8px 0 0 8px; display:flex; flex-direction:column; overflow:hidden; }
    #sidebar-head { padding:12px 14px; border-bottom:1px solid var(--border); display:flex; align-items:center; gap:8px; }
    #new-chat-btn { flex:1; background:var(--accent); color:#fff; border:none; border-radius:6px; padding:8px 10px; font-family:var(--font-mono); font-size:11px; letter-spacing:0.08em; text-transform:uppercase; cursor:pointer; transition:background .15s; }
    #new-chat-btn:hover { background:var(--accent2); }
    .side-section-label { font-size:9px; color:var(--text-muted); letter-spacing:2px; font-family:var(--font-mono); padding:12px 14px 6px; text-transform:uppercase; display:flex; align-items:center; justify-content:space-between; }
    .side-section-label button { background:transparent; border:1px solid var(--border2); color:var(--text-dim); border-radius:4px; width:20px; height:20px; cursor:pointer; font-size:12px; line-height:1; }
    .side-section-label button:hover { border-color:var(--accent); color:var(--accent); }
    #projects-list, #sessions-list { overflow-y:auto; }
    #sessions-list { flex:1; }
    .side-item { padding:8px 14px; cursor:pointer; border-left:2px solid transparent; display:flex; flex-direction:column; gap:2px; transition:background .12s; }
    .side-item:hover { background:var(--bg3); }
    .side-item.active { background:var(--bg3); border-left-color:var(--accent); }
    .side-item-title { font-size:12px; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .side-item-meta { font-size:9px; color:var(--text-muted); letter-spacing:0.04em; }
    .side-item-row { display:flex; align-items:center; gap:6px; }
    .side-item-row .side-item-title { flex:1; }
    .side-item-actions { display:none; gap:4px; }
    .side-item:hover .side-item-actions { display:flex; }
    .side-item-actions button { background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:11px; padding:0 2px; }
    .side-item-actions button:hover { color:var(--accent); }
    .proj-item { padding:7px 14px; cursor:pointer; font-size:12px; color:var(--text-dim); display:flex; align-items:center; gap:6px; border-left:2px solid transparent; }
    .proj-item:hover { background:var(--bg3); }
    .proj-item.active { color:var(--text); border-left-color:var(--cyan); background:var(--bg3); }
    .proj-item .dot { width:6px;height:6px;border-radius:50%;background:var(--cyan);flex-shrink:0; }
    #chatcol { flex:1; display:flex; flex-direction:column; min-width:0; }
    @media (max-width:760px){ #sidebar{display:none;} #workspace{padding:12px 8px;} }
"""
html = html.replace("    /* ── Scroll btn ── */", sidebar_css + "\n    /* ── Scroll btn ── */", 1)

# ── 2. Adjust #shell to live inside the chat column ─────────────────────────────
# The existing #shell becomes the chat column content; we wrap with workspace+sidebar.
shell_open = '<!-- ── Main ── -->\n<div id="shell">'
new_open = '''<!-- ── Main (Phase 7: sidebar + chat) ── -->
<div id="workspace" class="phase7-sidebar">
  <aside id="sidebar">
    <div id="sidebar-head">
      <button id="new-chat-btn">+ New Chat</button>
    </div>
    <div class="side-section-label">Projects <button id="new-proj-btn" title="New project">+</button></div>
    <div id="projects-list"></div>
    <div class="side-section-label">Conversations</div>
    <div id="sessions-list"></div>
  </aside>
  <div id="chatcol">
<div id="shell" style="height:auto;flex:1;max-width:none;margin:0;padding:0;">'''
html = html.replace(shell_open, new_open, 1)

# Close the extra chatcol/workspace divs after #shell closes.
# #shell ends right before the scroll button.
scroll_btn = '<button id="scroll-btn">↓ scroll</button>'
html = html.replace(scroll_btn, '</div></div>\n' + scroll_btn, 1)

# Make shell's inner height fill the column
html = html.replace(
    "#shell {\n      display: flex;\n      flex-direction: column;\n      height: calc(100vh - 52px);\n      max-width: 1200px;\n      margin: 0 auto;\n      padding: 20px 16px;\n    }",
    "#shell { display:flex; flex-direction:column; }",
    1
)

# ── 3. JS: sessions + projects logic, and expose chat hooks ─────────────────────
# We need sessionId + loadSession accessible. The chat IIFE keeps sessionId private,
# so we add a small bridge: expose window.SilexChat with needed methods.
# Insert bridge inside the chat IIFE right before its closing "})();\n\n// User dropdown"
bridge = '''
  // ── Phase 7 bridge ──
  window.SilexChat = {
    newChat: function(){ sessionId=null; clearFeed(); appendSys('new conversation'); intentBdg.textContent='—'; intentBdg.className=''; sessLbl.textContent='session: —'; window.SilexUI && window.SilexUI.markActive(null); },
    loadSession: async function(sid){
      try{
        var r=await fetch('/api/sessions/'+sid); if(!r.ok)throw 0;
        var d=await r.json();
        sessionId=sid; clearFeed();
        (d.turns||[]).forEach(function(t){ appendMsg(t.role==='assistant'?'silex':'user', t.content); });
        sessLbl.textContent='session: '+sid.slice(0,8)+'…';
        window.SilexUI && window.SilexUI.markActive(sid);
        scrollToBottom(true);
      }catch(e){ appendSys('could not load conversation',true); }
    },
    getProject: function(){ return window.SilexUI ? window.SilexUI.activeProject : null; },
    afterSend: function(){ window.SilexUI && window.SilexUI.refresh(); }
  };
  // hook send() to attach project + refresh list after each exchange
  var _origSend=send;
'''
html = html.replace("  checkStatus();\n})();", bridge + "  checkStatus();\n})();", 1)

# ── 4. The sidebar UI controller (separate IIFE appended before </script>) ──────
controller = '''
// ── Phase 7: sessions + projects sidebar ────────────────────────────────────
(function(){
  var sessionsList=document.getElementById('sessions-list');
  var projectsList=document.getElementById('projects-list');
  var newChatBtn=document.getElementById('new-chat-btn');
  var newProjBtn=document.getElementById('new-proj-btn');
  var activeSid=null;

  window.SilexUI={
    activeProject:null,
    markActive:function(sid){ activeSid=sid; renderSessionsActive(); },
    refresh:function(){ loadSessions(); }
  };

  function timeago(ts){
    if(!ts)return'';
    var s=Math.floor(Date.now()/1000-ts);
    if(s<60)return s+'s';
    if(s<3600)return Math.floor(s/60)+'m';
    if(s<86400)return Math.floor(s/3600)+'h';
    return Math.floor(s/86400)+'d';
  }

  function renderSessionsActive(){
    Array.prototype.forEach.call(sessionsList.children,function(el){
      el.classList.toggle('active', el.getAttribute('data-sid')===activeSid);
    });
  }

  async function loadSessions(){
    try{
      var r=await fetch('/api/sessions'); var d=await r.json();
      sessionsList.innerHTML='';
      (d.sessions||[]).forEach(function(s){
        var item=document.createElement('div');
        item.className='side-item'; item.setAttribute('data-sid',s.session_id);
        if(s.session_id===activeSid)item.classList.add('active');
        var row=document.createElement('div'); row.className='side-item-row';
        var title=document.createElement('div'); title.className='side-item-title'; title.textContent=s.title||'Untitled';
        var actions=document.createElement('div'); actions.className='side-item-actions';
        var renameB=document.createElement('button'); renameB.innerHTML='✎'; renameB.title='Rename';
        var delB=document.createElement('button'); delB.innerHTML='🗑'; delB.title='Delete';
        actions.appendChild(renameB); actions.appendChild(delB);
        row.appendChild(title); row.appendChild(actions);
        var meta=document.createElement('div'); meta.className='side-item-meta';
        meta.textContent=(s.turns||0)+' turns · '+timeago(s.last_activity);
        item.appendChild(row); item.appendChild(meta);

        item.addEventListener('click',function(e){
          if(e.target===renameB||e.target===delB)return;
          window.SilexChat.loadSession(s.session_id);
        });
        renameB.addEventListener('click',async function(e){
          e.stopPropagation();
          var nt=prompt('Rename conversation:',s.title||'');
          if(nt&&nt.trim()){ await fetch('/api/sessions/'+s.session_id+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:nt.trim()})}); loadSessions(); }
        });
        delB.addEventListener('click',async function(e){
          e.stopPropagation();
          if(!confirm('Delete this conversation? This cannot be undone.'))return;
          await fetch('/api/sessions/'+s.session_id,{method:'DELETE'});
          if(activeSid===s.session_id)window.SilexChat.newChat();
          loadSessions();
        });
        sessionsList.appendChild(item);
      });
      if(!(d.sessions||[]).length){
        var empty=document.createElement('div'); empty.className='side-item-meta'; empty.style.padding='10px 14px';
        empty.textContent='No conversations yet'; sessionsList.appendChild(empty);
      }
    }catch(e){}
  }

  async function loadProjects(){
    try{
      var r=await fetch('/api/projects'); var d=await r.json();
      projectsList.innerHTML='';
      // "All" pseudo-project
      var all=document.createElement('div'); all.className='proj-item'+(window.SilexUI.activeProject===null?' active':'');
      all.innerHTML='<span class="dot" style="background:var(--text-muted)"></span> All conversations';
      all.addEventListener('click',function(){ window.SilexUI.activeProject=null; loadProjects(); });
      projectsList.appendChild(all);
      (d.projects||[]).forEach(function(p){
        var item=document.createElement('div'); item.className='proj-item'+(window.SilexUI.activeProject===p.project_id?' active':'');
        item.innerHTML='<span class="dot"></span> '+p.name;
        item.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); });
        projectsList.appendChild(item);
      });
    }catch(e){}
  }

  newChatBtn.addEventListener('click',function(){ window.SilexChat.newChat(); });
  newProjBtn.addEventListener('click',async function(){
    var name=prompt('New project name:');
    if(name&&name.trim()){ await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim()})}); loadProjects(); }
  });

  // initial load + periodic refresh of session list
  loadProjects(); loadSessions();
  setInterval(loadSessions, 15000);
})();
'''
html = html.replace("</script>\n</body>", controller + "\n</script>\n</body>", 1)

# Refresh the sidebar after each completed exchange so new sessions appear
html = html.replace(
    "if(meta.intent){intentBdg.textContent=meta.intent;intentBdg.className='active';}",
    "if(meta.intent){intentBdg.textContent=meta.intent;intentBdg.className='active';}\n              if(window.SilexChat)window.SilexChat.afterSend();",
    1
)

open(path, "w", encoding="utf-8").write(html)
print("Phase 7 sidebar patched into index.html")
