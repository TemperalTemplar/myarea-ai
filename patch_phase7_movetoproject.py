#!/usr/bin/env python3
"""Phase 7d — add 'move to project' control on each conversation in the sidebar."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*move-to-project*/" in html:
    print("already applied — skipping")
    raise SystemExit(0)

# We need the projects list available to the session renderer. Cache it.
# Add a module-level cache populated by loadProjects.
html = html.replace(
    "  var activeSid=null;",
    "  var activeSid=null;\n  var projectsCache=[];",
    1
)

# Populate cache inside loadProjects (after we fetch d.projects)
html = html.replace(
    "      var r=await fetch('/api/projects'); var d=await r.json();\n      projectsList.innerHTML='';",
    "      var r=await fetch('/api/projects'); var d=await r.json();\n      projectsCache=d.projects||[];\n      projectsList.innerHTML='';",
    1
)

# Add a "move" button into the session actions (next to rename + delete).
old_actions = """        var renameB=document.createElement('button'); renameB.innerHTML='✎'; renameB.title='Rename';
        var delB=document.createElement('button'); delB.innerHTML='🗑'; delB.title='Delete';
        actions.appendChild(renameB); actions.appendChild(delB);"""
new_actions = """        var renameB=document.createElement('button'); renameB.innerHTML='✎'; renameB.title='Rename';
        var moveB=document.createElement('button'); moveB.innerHTML='📁'; moveB.title='Move to project';
        var delB=document.createElement('button'); delB.innerHTML='🗑'; delB.title='Delete';
        actions.appendChild(renameB); actions.appendChild(moveB); actions.appendChild(delB);"""
html = html.replace(old_actions, new_actions, 1)

# Wire the move button — prompt with a numbered list of projects + 0 for none.
move_handler = """        /*move-to-project*/moveB.addEventListener('click',async function(e){
          e.stopPropagation();
          var opts=['0) None (remove from project)'];
          projectsCache.forEach(function(p,i){ opts.push((i+1)+') '+p.name); });
          var choice=prompt('Move "'+(s.title||'chat')+'" to which project?\\n\\n'+opts.join('\\n')+'\\n\\nEnter a number:');
          if(choice===null)return;
          var n=parseInt(choice,10);
          var projectId=null;
          if(n>=1&&n<=projectsCache.length){ projectId=projectsCache[n-1].project_id; }
          else if(n!==0){ return; }
          await fetch('/api/sessions/'+s.session_id+'/project',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:projectId})});
          loadSessions();
        });"""

# Insert the move handler right after the rename handler block.
anchor = """        renameB.addEventListener('click',async function(e){
          e.stopPropagation();
          var nt=prompt('Rename conversation:',s.title||'');
          if(nt&&nt.trim()){ await fetch('/api/sessions/'+s.session_id+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:nt.trim()})}); loadSessions(); }
        });"""
html = html.replace(anchor, anchor + "\n" + move_handler, 1)

open(path, "w", encoding="utf-8").write(html)
print("move-to-project control added")
