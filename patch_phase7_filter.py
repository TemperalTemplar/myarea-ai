#!/usr/bin/env python3
"""Phase 7e — filter the Conversations list by the active project."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*proj-filter*/" in html:
    print("already applied — skipping")
    raise SystemExit(0)

# 1. The sessions API doesn't return project per session in the list response?
#    It DOES (list_sessions includes "project"). So we filter client-side.

# In loadSessions, after we get d.sessions, filter by active project.
old = "        sessionsList.innerHTML='';\n        (d.sessions||[]).forEach(function(s){"
new = """        sessionsList.innerHTML='';
        /*proj-filter*/var _ap=window.SilexUI.activeProject;
        var _list=(d.sessions||[]).filter(function(s){
          if(_ap===null) return true;          // All conversations
          return s.project===_ap;               // only this project
        });
        _list.forEach(function(s){"""
html = html.replace(old, new, 1)

# Also: when switching project, refresh the session list too.
# loadProjects currently only re-renders projects; make project clicks reload sessions.
html = html.replace(
    "      all.addEventListener('click',function(){ window.SilexUI.activeProject=null; loadProjects(); });",
    "      all.addEventListener('click',function(){ window.SilexUI.activeProject=null; loadProjects(); loadSessions(); });",
    1
)
html = html.replace(
    "        item.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); });",
    "        item.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); loadSessions(); });",
    1
)
# (project label click in the +button version)
html = html.replace(
    "        label.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); });",
    "        label.addEventListener('click',function(){ window.SilexUI.activeProject=p.project_id; loadProjects(); loadSessions(); });",
    1
)

open(path, "w", encoding="utf-8").write(html)
print("project filter on conversation list added")
