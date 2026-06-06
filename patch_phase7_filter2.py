#!/usr/bin/env python3
"""Phase 7e (corrected) — filter Conversations list by active project."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*proj-filter*/" in html:
    print("already applied — skipping")
    raise SystemExit(0)

# Match the EXACT text from the file.
old = """      sessionsList.innerHTML='';
      (d.sessions||[]).forEach(function(s){"""
new = """      sessionsList.innerHTML='';
      /*proj-filter*/var _ap=window.SilexUI.activeProject;
      var _slist=(d.sessions||[]).filter(function(s){ return _ap===null ? true : (s.project===_ap); });
      _slist.forEach(function(s){"""

if old not in html:
    print("ERROR: anchor text not found — file differs")
    raise SystemExit(1)

html = html.replace(old, new, 1)
open(path, "w", encoding="utf-8").write(html)
print("filter applied correctly")
