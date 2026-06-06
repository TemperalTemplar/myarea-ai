#!/usr/bin/env python3
"""Phase 7f — add a delete (×) control to each project in the sidebar."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*del-project*/" in html:
    print("already applied — skipping")
    raise SystemExit(0)

# Insert a delete button next to the per-project + button.
# Anchor: the block where 'plus' is created and appended.
old = """        var plus=document.createElement('button');
        plus.textContent='+'; plus.title='New chat in '+p.name;
        plus.style.cssText='background:transparent;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;line-height:1;padding:0 4px;';
        plus.addEventListener('mouseenter',function(){plus.style.color='var(--accent)';});
        plus.addEventListener('mouseleave',function(){plus.style.color='var(--text-muted)';});
        item.style.display='flex'; item.style.alignItems='center';
        item.appendChild(label); item.appendChild(plus);"""

new = """        var plus=document.createElement('button');
        plus.textContent='+'; plus.title='New chat in '+p.name;
        plus.style.cssText='background:transparent;border:none;color:var(--text-muted);cursor:pointer;font-size:14px;line-height:1;padding:0 4px;';
        plus.addEventListener('mouseenter',function(){plus.style.color='var(--accent)';});
        plus.addEventListener('mouseleave',function(){plus.style.color='var(--text-muted)';});
        var delp=document.createElement('button');
        delp.textContent='×'; delp.title='Delete project '+p.name;
        delp.style.cssText='background:transparent;border:none;color:var(--text-muted);cursor:pointer;font-size:15px;line-height:1;padding:0 4px;';
        delp.addEventListener('mouseenter',function(){delp.style.color='var(--accent)';});
        delp.addEventListener('mouseleave',function(){delp.style.color='var(--text-muted)';});
        /*del-project*/delp.addEventListener('click',async function(e){
          e.stopPropagation();
          if(!confirm('Delete project "'+p.name+'"?\\n\\nConversations in it are NOT deleted — they just become unfiled. The project\\'s memory collection is left intact.'))return;
          await fetch('/api/projects/'+p.project_id,{method:'DELETE'});
          if(window.SilexUI.activeProject===p.project_id){ window.SilexUI.activeProject=null; }
          loadProjects(); loadSessions();
        });
        item.style.display='flex'; item.style.alignItems='center';
        item.appendChild(label); item.appendChild(plus); item.appendChild(delp);"""

if old not in html:
    print("ERROR: anchor not found")
    raise SystemExit(1)

html = html.replace(old, new, 1)
open(path, "w", encoding="utf-8").write(html)
print("delete-project control added")
