#!/usr/bin/env python3
"""Phase 7b — make the chat send the active project id with each message."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

if "/*proj-send*/" in html:
    print("project-send already applied — skipping")
    raise SystemExit(0)

# In send(): after building body with session_id, attach project from SilexUI.
anchor = "if(sessionId)body.session_id=sessionId;"
addition = anchor + "\n    /*proj-send*/try{var _p=window.SilexUI&&window.SilexUI.activeProject;if(_p)body.project=_p;}catch(e){}"
html = html.replace(anchor, addition, 1)

open(path, "w", encoding="utf-8").write(html)
print("project-send patched")
