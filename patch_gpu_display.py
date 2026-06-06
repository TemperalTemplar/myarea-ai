#!/usr/bin/env python3
"""Patch index.html to show GPU temp in the status bar — safe, no feed wipe."""
path = "/home/temp/myarea-ai/app/ui/templates/index.html"
html = open(path, encoding="utf-8").read()

# 1. Add a GPU temp span to the status bar (after model-label)
if 'id="gpu-temp"' not in html:
    html = html.replace(
        '<span id="model-label">—</span>',
        '<span id="model-label">—</span>\n    <span class="sep">|</span>\n    <span id="gpu-temp">GPU —</span>',
        1
    )

# 2. Add a SEPARATE lightweight poller that ONLY updates gpu-temp.
#    Does not touch the feed. Inserted right before checkStatus() is first called.
if "/*gpu-poll*/" not in html and "checkStatus();" in html:
    poller = (
        "  /*gpu-poll*/function pollGpu(){"
        "fetch('/api/status').then(function(r){return r.json();}).then(function(d){"
        "var gt=document.getElementById('gpu-temp');"
        "if(gt){gt.textContent=(d.gpu_temp!=null?('GPU '+d.gpu_temp+'\\u00b0C'):'GPU —');"
        "gt.style.color=(d.gpu_temp!=null&&d.gpu_temp>=60)?'#e63946':(d.gpu_temp!=null&&d.gpu_temp>=52?'#f59e0b':'#00d4a0');}"
        "}).catch(function(){});}"
        "pollGpu();setInterval(pollGpu,5000);\n  checkStatus();"
    )
    html = html.replace("  checkStatus();", poller, 1)

open(path, "w", encoding="utf-8").write(html)
print("index.html patched for GPU temp display (safe poller)")
