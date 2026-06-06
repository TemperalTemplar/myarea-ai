#!/usr/bin/env python3
"""Phase 7c — add a + button on each project to start a new chat in it."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*proj-newchat*/" in html:
    print("already applied — skipping")
    raise SystemExit(0)

# Replace the project item builder to include a + button that starts a new chat in that project.
old = """      (d.projects||[]).forEach(function(p){
        var item=document.createElement('div'); item.className='proj-item'+(window.SilexUI.activeProject===p.project_id?' active':'');
        item.innerHTML='<span class="dot"></span> '+p.name;
        item.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); });
        projectsList.appendChild(item);
      });"""

new = """      (d.projects||[]).forEach(function(p){
        var item=document.createElement('div'); item.className='proj-item'+(window.SilexUI.activeProject===p.project_id?' active':'');
        var label=document.createElement('span'); label.style.flex='1'; label.style.display='flex'; label.style.alignItems='center'; label.style.gap='6px';
        label.innerHTML='<span class="dot"></span> '+p.name;
        var plus=document.createElement('button');
        plus.textContent='+'; plus.title='New chat in '+p.name;
        plus.style.cssText='background:transparent;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;line-height:1;padding:0 4px;';
        plus.addEventListener('mouseenter',function(){plus.style.color='var(--accent)';});
        plus.addEventListener('mouseleave',function(){plus.style.color='var(--text-muted)';});
        item.style.display='flex'; item.style.alignItems='center';
        item.appendChild(label); item.appendChild(plus);
        label.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); });
        /*proj-newchat*/plus.addEventListener('click',function(e){
          e.stopPropagation();
          window.SilexUI.activeProject=p.project_id;
          window.SilexChat.newChat();
          loadProjects();
        });
        projectsList.appendChild(item);
      });"""

html = html.replace(old, new, 1)
open(path, "w", encoding="utf-8").write(html)
print("per-project + button added")
