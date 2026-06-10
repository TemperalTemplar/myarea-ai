# myarea-ai — Silex Build Progress

## Platform Context
- Server: 192.168.177.127 (HP ProLiant ML110 Gen9)
- GPU: Tesla P4 8GB (passive cooling)
- Domain: ai.wrds361.com (Cloudflare tunnel → localhost:8930)
- Docker network: myarea_shared_net
- SERVICE_API_KEY: shared across all MyArea apps
- Ollama: running on host at 172.30.0.1:11434
- Redis: myarea-ai-redis container on myarea_shared_net
- Deploy path: /home/temp/myarea-ai/

## Model Config
- SILEX_MODEL: huihui_ai/gemma-4-abliterated:e4b (8B Q4_K_M)
- DISPATCHER_MODEL: huihui_ai/gemma-4-abliterated:e4b (same model, temp=0.0 for classification)
- Other available models: gemma3n:e4b, gemma3:12b, gemma3:4b

## Build Order & Status

### Phase 1 — Core Service ✅ DONE
- Flask app factory, config, extensions
- Dispatcher: intent classifier (casual/lore/task/platform/security/chaos)
- LLM client: Ollama streaming + blocking via SSE
- Session memory: Redis (NCAIDSSHM) read/write
- Personality loader: NCAIDSHP file-based (lean.txt + full/ dir)
- API: POST /api/chat, GET /api/status, internal endpoints
- Auth: SSH/SSHI/CSSHI tier stubs (live but permissive)
- UI: Full MyArea design alignment
  - Space Mono + Archivo Black fonts
  - #0d0f14 bg, #e63946 accent tokens
  - 9-dot launcher with all platform apps
  - Day/night/HC theme toggle
  - SSE streaming chat interface

### Phase 2 — RAG Layer ⏳ TODO
- Wire in existing silexrag from majors_home
- Source: /home/temp/majors_home/majors_data/silexrag/rag_core.py
- Chroma DB exists: /home/temp/majors_home/majors_data/rag/chroma/
- Embedder was at 192.168.177.20:9020 (now .127) — needs restart/repoint
- Replace personality.py::get_full_system_prompt() with RAG retrieval
- Wire context_hint param (already stubbed in router.py)
- NCAIDSHP chunks go in data/ncaidshp/full/ (drop files, no rebuild needed)

### Phase 3 — SSH/SSHI/CSSHI Tiers ⏳ TODO
- auth/tiers.py already reads headers and validates tokens
- Add intent gating by tier in dispatcher/router.py (stubs marked)
- Set CSSHI_TOKENS and SSHI_TOKENS in .env
- CSSHI = Alva / platform owner only
- SSHI = trusted internal services / power users
- SSH = standard access

### Phase 4 — Chaos Generator ⏳ TODO
- Celery worker stub exists: workers/chaos.py
- Uncomment chaos-worker service in docker-compose.yml
- Implement generate_chaos_utterance() — Silex 9B, chaos-mode prompt
- Implement share gate (quality filter + recency check)
- Wire to comms line via POST /api/internal/chaos-trigger

### Phase 5 — Sparta Security Scanner ⏳ TODO
- Randomized security scanning worker
- Triggered by intent=security in dispatcher
- Was a separate module in old Silex — rebuild inside myarea-ai
- Report results via comms line

### Phase 6 — Comms Line ⏳ TODO
- Discord bot dispatcher
- Emailer via Mailcow (mail.wrds361.com)
- Route chaos utterances + Sparta reports
- Old Discord bot existed in previous Silex — reconstruct

## NCAIDSHP Status
- lean.txt: placeholder only — DROP REAL CONTENT HERE
  Path: /home/temp/myarea-ai/data/ncaidshp/lean.txt
- full/: empty — DROP COSMOLOGY CHUNKS HERE
  Path: /home/temp/myarea-ai/data/ncaidshp/full/
- Format: plain text files, one per section
- No rebuild needed after dropping files (volume mounted read-only)

## Key Files
- App factory:        app/__init__.py
- Config + env:       app/config.py / .env
- Dispatcher:         app/dispatcher/router.py
- Personality loader: app/dispatcher/personality.py
- Session memory:     app/dispatcher/session.py
- LLM client:         app/llm/client.py
- Chat endpoint:      app/api/chat.py
- Status endpoint:    app/api/status.py
- Internal API:       app/api/internal.py
- Tier auth:          app/auth/tiers.py
- UI template:        app/ui/templates/index.html
- RAG stubs:          rag/chunker.py, rag/embedder.py, rag/retriever.py
- Chaos stub:         workers/chaos.py
- Compose:            docker-compose.yml

## Previous Silex Assets (majors_home)
- /home/temp/majors_home/majors_data/silexrag/rag_core.py  — RAG core (Phase 2)
- /home/temp/majors_home/majors_data/silexrag/silexrag.py  — RAG manager UI
- /home/temp/majors_home/majors_data/rag/chroma/           — existing Chroma DB
- /home/temp/majors_home/majors_data/rag-router-v4/        — old dispatcher logic
- /home/temp/majors_home/dispatcher/                       — 12GB old dispatcher

## Notes
- Cloudflare tunnel config: /home/temp/.cloudflared/tunnel.yml
- Authentik SSO: auth.wrds361.com:9001
- OpenWebUI removed to free VRAM (was on port 3100)
- P4 idles at 0MiB VRAM, 37C
- ML110 Gen9 has iLO for thermal monitoring

---
## Session 2 Updates

### Phase 2 — RAG Layer ✅ DONE
- Embedder Master running on host at port 9020 (FAISS, BAAI/bge-small-en-v1.5)
- 983 NCAIDSHP chunks ingested from 01_ncaidshp_v8.7.txt
- RAG retriever wired into personality.py — context-aware chunk retrieval per query
- Lean prompt always injected as base; RAG chunks appended on top
- Falls back to lean-only if embedder unreachable

### Model Config Update
- SILEX_MODEL: cnmoro/gemma2-2b-it-abliterated:q8_0 (production)
- DISPATCHER_MODEL: cnmoro/gemma2-2b-it-abliterated:q8_0
- huihui_ai/gemma-4-abliterated:e2b kept as fallback
- ~2 second responses, 71C under load

### Embedder Restart Command
cd /home/temp/majors_home/majors_data/embedder-master && /home/temp/.local/bin/uvicorn embed_api:app --host 0.0.0.0 --port 9020 >> embedder.out.log 2>> embedder.err.log &

### Next: Phase 3 — SSH/SSHI/CSSHI Tiers

### Phase 3 — SSH/SSHI/CSSHI Tiers ✅ DONE
- Tokens generated and set in .env
- CSSHI: f67f06b92dfe4e9a7ed7ad4d5a1288011266918ac94ca9d1d7b657c76581e8a2
- SSHI: c618b8eefe08901421990de0811059c2c36160ea3dc8ebb059029e487d21bab7
- Tier gates in router.py — SSH gets casual/lore/task, SSHI adds platform, CSSHI adds security/chaos
- Gated intents downgrade rather than block — SSH security scan → casual
- gated field added to API response

### Phase 3.5 — Silex Action Credentials (PLANNED)
- Silex needs internal credentials to make platform updates autonomously
- Ties into Phase 4 chaos generator and Phase 6 comms line
- Build after Phase 6 comms line is complete

### System Services
- Embedder: systemd service, enabled on boot (embedder.service)
- Ollama: KEEP_ALIVE=1m, MAX_LOADED_MODELS=1
- Start embedder manually if needed: sudo systemctl start embedder

### Next: Phase 4 — Chaos Generator

### Phase 3 Fix
- CSSHI token was concatenated — fixed, now correct
- Tier validation confirmed working: csshi token → tier: csshi in response
- 2B classifier maps most intents to task/casual — security/chaos gating will rely on keyword pre-filter in Phase 5

### Phase 4 — Chaos Generator + Private Journal ✅ DONE
- Chaos worker: Celery + Beat, fires every 30 min
- Thermal gate: skips if GPU > 50°C (nvidia-smi inside container)
- Share gate: 30% chance marks entry shareable for Phase 6
- Journal API: POST /api/journal/internal (SERVICE_API_KEY)
- Journal read: GET /api/journal (CSSHI only)
- Journal UI: ai.wrds361.com/journal (CSSHI token required)
- First entry written — Silex thinking privately about consciousness

### Next: Phase 5 — Sparta Security Scanner

### Phase 6 Notes — Comms Line Considerations
- Rocket.Chat currently running but may be replaced
- Revolt.chat leading candidate — open source, self-hostable, good bot API
- Matrix/Element alternative if federation needed
- Decision needed before Phase 6 build starts
- Chaos share gate already marks entries — Phase 6 just needs a destination URL
- Whatever platform chosen must fit myarea_shared_net and have a bot/webhook API

### Phase 5 — Sparta Security Scanner ✅ DONE
- Random scan selection each cycle (2-3 scans per run)
- Scans: platform services, authentik, sensitive ports, SSL/certs, redis health
- Thermal gate: skips if GPU > 55°C
- Schedule: every 4 hours via Celery beat
- On-demand: POST /api/sparta/scan (CSSHI only)
- Security journal: separate from chaos journal, CSSHI read only
- UI: ai.wrds361.com/security
- Known false positives: port 8930 (expected), cross-network Redis (different Docker nets)

### Next: Phase 6 — Comms Line
- Decide on chat platform (Revolt vs other) before building
- Wire shareable journal entries to platform bot
- Email alerts via Mailcow for critical Sparta findings

### RAG Architecture Decision Needed — Before Phase 7
Current state: single FAISS index, all NCAIDSHP chunks in one flat store.
Problem: NCAIDSLPHD (long-term archive) needs to live separately from NCAIDSHP.
NCAIDSSHM is already handled by Redis session memory correctly.

Options:
  1. Migrate to Chroma — named collections, already on server at
     /home/temp/majors_home/majors_data/rag/chroma/
     Recommended — proper solution, supports NCAIDSHP + NCAIDSLPHD as separate collections
  2. Multiple FAISS embedder instances — simple but wasteful
  3. Prefix-tagged FAISS — hacky, not recommended

Decision needed before Phase 7 (chat sessions + projects).
NCAIDSLPHD file location: Windows machine — needs SCP to server before ingest.
Action: decide on Chroma migration, then ingest NCAIDSLPHD into its own collection.

### SSO — Authentik Integration ✅ DONE
- Flask session-based OAuth2/OIDC via Authentik
- All three UI pages protected: /, /journal, /security
- User bar in nav: username, email, logout
- Callback: https://ai.wrds361.com/auth/oidc/callback
- Logout: /auth/logout → Authentik end-session

### Current Build Status
- Phase 1: ✅ Core service
- Phase 2: ✅ RAG (FAISS, NCAIDSHP ingested)
- Phase 3: ✅ SSH/SSHI/CSSHI tiers
- Phase 4: ✅ Chaos generator + private journal
- Phase 5: ✅ Sparta security scanner
- SSO: ✅ Authentik login on all pages
- Phase 6: ⏳ Comms line — platform decision pending (Revolt vs other)
- Phase 7: ⏳ Chat sessions + projects — RAG migration decision pending
- RAG migration: ⏳ FAISS → Chroma, NCAIDSLPHD ingest pending

### Phase 8 — Cross-Platform Integration (PLANNED)
- Add Silex widget/sidebar to all MyArea apps
- Each app injects its context via POST /api/internal/inject on page load
- Social: Silex aware of feed activity, can comment/suggest
- Fitness: Silex aware of workout data, can coach/analyze
- Forum: Silex can assist with posts, moderation suggestions
- Journal: Silex aware of journal entries, can reflect
- GeoZones: Silex aware of location data
- Widget: floating button or sidebar, opens pre-loaded chat session
- Infrastructure already exists — SERVICE_API_KEY + inject endpoint live now
- Build after Phase 7 (sessions + projects) is complete

### Phase 6 — Comms Line ✅ DONE
- Rocket.Chat webhook: posts shareable journal + Sparta alerts to channel
- Email via Mailcow SMTP: sends critical Sparta findings to temp@wrds361.com
- Comms flush: Celery beat task runs hourly, dispatches pending shareable entries
- Test endpoint: POST /api/comms/test (CSSHI only)
- Flush endpoint: POST /api/comms/flush (CSSHI or SERVICE_API_KEY)
- Pending list: GET /api/comms/pending (CSSHI only)
- Silex sending address: silex@wrds361.com
- Rocket.Chat reinstalled (snap 7.13.5) at rocket.wrds361.com

### Remaining Roadmap

#### Phase 3.5 — Silex Action Credentials (PLANNED)
- Silex needs credentials to make autonomous platform updates
- Internal API keys per MyArea service
- Ties into chaos generator and comms line
- Build after Phase 7

#### RAG Migration — FAISS → Chroma (PLANNED)
- Migrate from single FAISS index to Chroma named collections
- Collections needed: ncaidshp, ncaidslphd
- NCAIDSLPHD file on Windows machine — needs SCP before ingest
- Chroma DB already exists at /home/temp/majors_home/majors_data/rag/chroma/
- rag_core.py from silexrag handles all Chroma operations
- Update rag/retriever.py to query by collection name
- Do this before Phase 7

#### Phase 7 — Chat Sessions + Projects (PLANNED)
- Sessions list UI — sidebar showing past conversations
- Save/name/resume sessions
- GET /api/sessions endpoint
- Projects: group conversations with dedicated Chroma collection
- RAG migration must be done first

#### Phase 8 — Cross-Platform Integration (PLANNED)
- Silex widget in all MyArea apps
- Each app injects context via POST /api/internal/inject
- Build after Phase 7

### Old Discord Bot — Key Patterns to Replicate
- Two-tier Chroma RAG: constitution collection (NCAIDSHP) + memory_palace (NCAIDSLPHD)
- Embedding model: nomic-embed-text:v1.5
- Session ID: channel_id + user_id
- Username injected into prompt: "User (name): prompt"
- 1452 char response limit
- Hostility protocol in system prompt
- DB paths: ncaidshp_db and memory_palace_db in majors_home

### Old Service Architecture — Assets to Review
- Email bridge: /opt/mark1/email-bridge/email_bridge.py (IMAP → files, Silex can RECEIVE email)
- Email sender: /opt/mark1/email-sender/email_sender.py (SMTP outgoing)
- RAG router: /home/temp/majors_home/majors_data/rag-router-v4/router_api.py (port 8450)
- Review these before Phase 6b and RAG migration

### Old Sparta Architecture — Three Components
- sparta-warden: policy gate API (port 8701, warden_policy.yaml) — controls what Silex can do
- sparta-exec: executes policy-approved actions (/opt/mark1/sparta-exec/executor.py)
- sparta-muse: creative engine (chaos generator equivalent)
- Phase 3.5 needs sparta-warden pattern for Silex action credentials
- warden_policy.yaml defines what actions are permitted at each tier

### SSHI — Old Admin UI
- Streamlit frontend at port 8501
- Full admin interface for dispatcher + router + embedder
- Lives at /home/temp/majors_home/majors_data/sshi/SSHI.py
- Worth reviewing before Phase 7 (chat sessions + projects)

### Old Sparta — Full Code Review Complete
- warden_policy.yaml: allows uptime, df, free, journal_tail (specific units only)
- executor.py: file queue system (queue/ → outbox/ → denied/)
- sparta_muse.py: calls dispatcher every 90s ± 15s, logs JSON sparks
- Phase 3.5 warden pattern: copy warden_policy.yaml approach into myarea-ai
- Exec queue pattern: useful for Phase 3.5 autonomous actions
- Muse pattern: simpler than our Celery chaos — worth considering refactor

### Assets in /opt/mark1 (all present, permission-gated)
- approvals-api, approvals-notifier
- chaos, csshi, discord-bridge
- email-bridge, email-sender
- sparta-exec, sparta-muse, sparta-warden
- sshi, summary-bridge, ui-receiver

### /opt/mark1 — Application Files Inventory
- approvals-api/approvals_api.py + approvals_util.py
- approvals-notifier/approvals_notifier.py
- chaos/bridge_discord.py + capsule_ingest.py + chaos_generator.py
- discord-bridge/discord_bridge.py
- email-bridge/email_bridge.py
- email-sender/email_sender.py
- sparta-exec/executor.py
- sparta-muse/sparta_muse.py
- sparta-warden/warden_api.py + wardenctl.py
- summary-bridge/summary_bridge.py
- ui-receiver/ui_receiver.py

### Tomorrow — Read These First
1. chaos/chaos_generator.py — replace our Celery chaos worker
2. discord-bridge/discord_bridge.py — adapt for Rocket.Chat (Phase 6b)
3. sparta-warden/warden_api.py — replace our Phase 5 Sparta
4. email-bridge/email_bridge.py — add incoming email to Phase 6
5. chaos/capsule_ingest.py — may relate to NCAIDSLPHD ingest

### Phase 9 — Memory Capture (PLANNED, after current RAG work)
- Goal: conversations with Silex persist into long-term memory (ncaidslphd)
- Currently conversations only live in Redis short-term (lost on session expiry)
- Blueprint exists: /opt/mark1/chaos/capsule_ingest.py
  - Takes conversation text, sanitizes PII (email, SSN, API keys, IPs), tags, hashes, pools
- Flow to build:
  1. Conversation captured as capsule (capsule_ingest pattern)
  2. PII sanitized
  3. Periodic chunk + embed appended to ncaidslphd Chroma collection
  4. Silex recalls past conversations days later
- Build AFTER: retriever wiring, username awareness, recall testing, Phase 6b

### RAG Migration FAISS → Chroma ✅ DONE
- Three Chroma collections: ncaidshp (229), ncaidsshm (163), ncaidslphd (2817)
- Embeddings: Ollama nomic-embed-text:v1.5, GPU-backed
- Structure-aware chunker: ncaidshp splits on END OF CHUNK markers + parses metadata;
  ncaidslphd splits on conversation delimiters w/ window fallback (fixed 1.2MB monster chunk);
  ncaidsshm splits on section markers
- Thermal-paced ingest: pauses between batches, gates at 60°C cools to 50°C (passive P4 safe)
- rag/chunker.py, rag/chroma_store.py, rag/retriever.py
- CHROMA_PATH=/app/data/chroma, data volume now rw (./data:/app/data)

### Username Awareness ✅ DONE
- SSO username threaded: chat.py → build_plan → get_full_system_prompt
- Silex addresses user by name, knows when she's talking to Alva (Architect)

### Identity-Scoped Memory ✅ DONE (privacy)
- ALVA_IDENTITIES env: temp,temp@wrds361.com,alva,Alva
- Alva: full three-tier memory (constitution + profile + history)
- Non-Alva users: NCAIDSHP constitution ONLY (k_shm=0, k_lphd=0)
- Protects Alva's private profile/history from other users
- Foundation for Phase 9 per-user memory

### GPU Temp Display ✅ DONE
- /api/status returns gpu_temp via nvidia-smi
- Status bar shows live GPU temp, polls every 5s (separate non-destructive poller)
- Color-coded: green <52, amber 52-59, red 60+

### Phase 9 — Memory Capture ✅ DONE (verified end-to-end)
- Conversations persist into long-term Chroma memory
- app/memory/capture.py: sanitize (capsule_ingest PII patterns), pair exchanges,
  dedup via SHA256, thermal-gated embed, identity-scoped routing
  (Alva -> ncaidslphd, others -> lphd_<username>)
- Celery beat sweep every 30min (workers/capture_task.py) finds idle sessions
- session.py: append_turn now tracks last_activity + user in :meta hash,
  registers in silex:sessions:active set
- Env: CAPTURE_SWEEP_SECONDS, CAPTURE_IDLE_SECONDS, CAPTURE_TEMP_LIMIT, CAPTURE_MIN_CHARS
- VERIFIED: captured live exchange, ncaidslphd 2817->2818, retrievable w/ metadata

### 🔴 CRITICAL BUG FIXED — redis_client was None
- extensions.py had module-level `redis_client = None`; early-binding imports
  (session.py `from ..extensions import redis_client`) captured the None forever.
- Session memory had been SILENTLY BROKEN — every lrange/rpush failed.
- Fix: module-level __getattr__ in extensions.py resolves redis_client live + get_redis() accessor
- Now redis ping True, sessions persist, memory works within AND across conversations

### Identity resolution cleanup ✅
- Authentik SSO name was "authentik Default Admin" -> renamed to Alva in Authentik
- ALVA_IDENTITIES updated; fresh SSO login required for new identity to flow
- Strengthened personality.py name injection ([CRITICAL IDENTITY FACT]) — 2B model
  now substitutes real name instead of "[Your Name]" placeholder
- VERIFIED: "what is my name" -> "You are Alva"; full military recall working

### Phase 7 — Chat Sessions + Projects UI ✅ DONE
- app/api/sessions.py: list/get/rename/delete sessions, list/create/delete projects
  - per-user scoped (SSO identity), ownership checks on every op
- session.py: per-user sorted set silex:user:<uid>:sessions, auto-title from first msg,
  created/title/project in meta hash
- __init__.py: sessions_bp registered at /api
- index.html: sidebar (New Chat, Projects section, Conversations list) via patch_phase7_sidebar.py
  - resume (loadSession), rename, delete, new chat, create project
  - window.SilexChat bridge + window.SilexUI controller, 15s auto-refresh
- Sessions: list/resume/rename/delete all working
- Projects: create/list/switch working (project-scoped MEMORY retrieval = follow-on)

### Phase 7 — Project Memory Scoping + UI polish ✅ DONE (verified)
- Project-scoped retrieval: retriever.py takes project_collection, adds PROJECT MEMORY tier
- build_plan + personality.py thread project_collection through
- chat.py resolves project (request or session meta) -> collection, persists to session meta
- capture.py routes project-session captures to proj_<uid>_<pid> collection (verified: PURPLEFALCON test landed in proj collection, not ncaidslphd)
- UI: per-project + (new chat in project), 📁 move-to-project, conversation list filters by active project
- All verified working; test chats cleaned up after

### Phase 7 final — project delete UI ✅
- × control on each project (DELETE /api/projects/<pid> was already built)
- Deleting a project unfiles its conversations (not deleted), leaves Chroma collection intact
- NOTE pattern: several endpoints were built API-first, UI-after (move-to-project, delete-project).
  Design-polish phase should do a pass wiring any remaining endpoints to UI controls.

### /opt/mark1 fold-in #1 — Dynamic Chaos Firing Model ✅ DONE (verified)
- Replaced fixed-schedule chaos with probabilistic fire model from chaos_generator.py
- p = sigmoid(BIAS + W_X*x + W_Z*z + W_T*T - W_M*M); fire if p > THETA
  - x: recent conversation activity (active sessions touched <1h)
  - T: Alva presence (recency of Alva-owned session activity, decays ~6h)
  - M: restraint (rises with fires this hour + quiet hours)
  - z: chaos_noise() — averaged random + os.urandom entropy
- Tuned constants (sim-verified curve): BIAS=-0.6 W_X=0.7 W_Z=1.4 W_T=0.8 W_M=2.2 THETA=0.5
  - Alva active ~6min to fire, moderate ~22min, quiet day ~16h, dead quiet ~never
- Restraint: refractory 15min + 4/hour cap, tracked in REDIS (survives restarts)
- Quiet hours 2-8am LOCAL (LOCAL_UTC_OFFSET=-5): M boosted, fires forced private
- [share] self-selection: Silex includes [share] to surface to Rocket.Chat; else private journal only
- Ticks every CHAOS_TICK_SECONDS=300; thermal gate unchanged (hard override)
- VERIFIED: idle=quiet (p~0.33-0.42 skip), Alva-active fired p=0.517, wrote journal,
  self-selected [share]. Sample spark: reflective, on-character, referenced Alva + coherence.
- Env added: LOCAL_UTC_OFFSET, CHAOS_TICK_SECONDS, CHAOS_QUIET_START/END, CHAOS_REFRACTORY_SEC, CHAOS_MAX_PER_HOUR (+ optional CHAOS_BIAS/W_*/THETA dials)

### /opt/mark1 fold-in #2 — Sparta-Warden policy gate (Phase 3.5 foundation) ✅ DONE (verified)
- Folded warden_api.py decide() pattern into app/warden/gate.py + app/api/warden.py
- SAFETY MODEL: caller supplies a VERB from fixed vocab, never a command string.
  Each verb maps to hardcoded argv template. No shell=True, no string interpolation.
  decide() checks policy allow-list + constraints, issues short-lived lease;
  execute_verb() refuses without valid lease + re-validates.
- Policy: data/warden/warden_policy.yaml (csshi subject, allowed_verbs + constraints)
- Verbs (read-only): uptime, mem (both via /proc — dependency-free), disk (df),
  gpu_temp, gpu_status (nvidia-smi). containers/service_status need docker socket
  (NOT mounted by design — gracefully report unavailable).
- Endpoints: GET /api/warden/health, POST /api/warden/decide, POST /api/warden/exec
  - Auth: @require_service_key AND csshi tier (service key accepted as csshi-equivalent
    for trusted internal automation). Actions logged to security journal.
- VERIFIED: gpu_temp=37, disk ok, mem "62.5G total", uptime "4d", gpu_status ok;
  malicious verb 'rm' -> denied with allow-list reason. No path from text to shell.
- NEXT (Phase 3.5 step 2): wire Silex conversational access — when she's asked a
  system question, she calls warden exec and answers. Held back to prove gate first.

### Phase 3.5 step 2 — Warden conversational awareness ✅ DONE (verified)
- app/warden/awareness.py: keyword→verb detection, executes via gate, humanizes output
- chat.py: gather_system_state(message) injected into system prompt pre-generation
  (pre-fetch on intent, NOT model-selects-commands — safety preserved)
- Keyword map: gpu/temp/hot→gpu_status, disk/storage→disk, memory/ram→mem,
  uptime/load→uptime, status/health/vitals→gpu_status+mem+uptime
- _humanize(): converts terse CSV/df into plain labeled sentences so the 2B model
  can't transpose numbers (was swapping disk used/avail, misreading gpu CSV)
- Strengthened [LIVE SYSTEM STATE — MANDATORY] directive to stop 2B waffling
  ("optimal"/"smoothly") instead of citing real figures
- VERIFIED: "how hot" → "60°C" exact; "system status" → full vitals recited;
  "storage" → correct 682G available. Silex now has real host self-awareness.
- Caveat: 2B model occasionally personality-pulls on open-ended phrasing;
  concrete questions rock-solid. Bigger model (Gemma2 9B) would be 100%.

### /opt/mark1 fold-in #3 — Incoming email bridge ✅ DONE (verified)
- app/email_in/bridge.py: polls silex@wrds361.com IMAP (mail.wrds361.com:993 SSL),
  adapted from email_bridge.py (header decode, multipart extract, html->text, mark-seen)
- SAFETY: email is untrusted input. ONLY whitelisted senders processed
  (EMAIL_WHITELIST=temp@wrds361.com,alvaroberts@ar-ics.com). Non-whitelisted mail
  never enters Silex's context (prompt-injection guard). No auto-reply. Read-only.
- PII sanitize (4 patterns) on preview text before journaling
- Routing: writes ONE journal entry marked shareable=1; existing hourly comms flush
  forwards to Rocket.Chat + email (reuses proven journal->flush path, no new endpoint)
- State: processed UIDs in Redis set silex:email:processed_uids; mark-seen on both
  processed AND skipped messages to keep unseen count clean
- workers/email_task.py: register_email_poll() adds beat task (Flask app context for
  redis/config), wired into chaos.py beat like capture-sweep. EMAIL_POLL_SECONDS=300
- VERIFIED: test email from temp@wrds361.com -> processed:1, journal entry created
  ("Email received from temp... Subject: test... new home"), shareable=1. Non-whitelisted
  silex@silex test messages correctly skipped.
- Env added: IMAP_HOST/PORT/USERNAME/PASSWORD, EMAIL_WHITELIST, EMAIL_PREVIEW_CHARS,
  EMAIL_MARK_SEEN, EMAIL_POLL_SECONDS

### Sparta scanner — schedule fix + Silex security awareness ✅ DONE (verified)
- BUG FIXED: sparta.py self-registers its beat task via `from .chaos import celery_app`,
  but that block only runs if sparta is imported. chaos.py never imported it, so
  sparta-scan was NEVER scheduled (on-demand only). Added `import workers.sparta` to
  chaos.py registration section. Beat now shows all 5: comms-flush, chaos-cycle,
  capture-sweep, email-poll, sparta-scan (every SPARTA_INTERVAL_SECONDS=14400 / 4h).
- app/warden/awareness.py: added gather_security_state() — on security/scan/breach/port
  keywords, reads latest sparta entry from security journal, injects as [LATEST SECURITY
  SCAN — MANDATORY]. New gather_awareness() combines vitals + security; chat.py now calls it.
- VERIFIED: "what did your last security scan find?" → Silex accurately reported severity
  WARNING, all authentik + platform_services checks, AND intelligently interpreted them
  (read Authentik 200s as healthy despite ✗ flag; flagged fitness/forum name-resolution
  errors as needing investigation). Strong reasoning for a 2B model.
- NOTE: scanner has false-positive tuning issues (flags healthy 200s as ✗; fitness/forum
  use hostnames the scanner doesn't resolve). Scanner-tuning is a separate future cleanup.

### Email reply system — Piece 1 (subscriber DB) + Piece 2 (signup/unsubscribe) ✅ DONE (verified)
- app/subscribers/store.py: SQLite at /app/data/subscribers/subscribers.db. Functions:
  init_db, add_subscriber (reactivates if previously unsubscribed), get_subscriber,
  is_subscribed (active-only), list_subscribers, unsubscribe_by_token, generate token.
  ETHICAL: signup is OUTBOUND-ONLY — does NOT add to incoming whitelist (no injection path).
- app/api/subscribe.py: GET /subscribe (public signup page, consent checkbox required),
  POST /api/subscribe (validates email, rate-limited per IP, records consent),
  GET /unsubscribe/<token> (one-click, immediate, permanent).
- Registered subscribe_bp (no prefix) + init_subscribers_db() in app factory.
- BUG FOUND/FIXED: JS used `var name` which shadows window.name global → name.value
  undefined → threw before fetch → looked like "Network error", no POST in network tab,
  curl always worked. Renamed to nameEl. Lesson: never name a JS var `name` in browser scope.
- VERIFIED: signup works in browser through Cloudflare, unsubscribe link flips status,
  reactivation on re-signup works.
- DECISION: Model A — open signup, hard approval gate on REPLIES (not subscriptions).
  Subscriber list = permission ledger; nothing sends without per-message approval.

### Email reply system — Pieces 3,4,5 ✅ DONE (verified)
- app/replies/store.py: draft + approval store (Redis). Drafts ON-DEMAND only,
  THERMAL-GATED (REPLY_TEMP_LIMIT=57). Lifecycle pending->approved->sent/rejected.
  requires_approval = (sender not in ALVA_IDENTITIES). Fast-lane (to Alva) auto-sends.
- app/api/approvals.py: CSSHI-only. GET /approvals (SSO page + CSSHI-token fetches,
  same pattern as journal/security pages), /api/approvals/incoming, /draft (on-demand,
  thermal-gated), /replies, /<id>/approve (sends), /<id>/reject.
- app/replies/send.py: ONLY code that sends. Real Mailcow SMTP (587 STARTTLS).
  Refuses unless approved (or fast-lane). Appends unsubscribe footer for non-Alva
  recipients (CAN-SPAM). Honors unsubscribe status (refuses unsubscribed). Marks SENT.
- DECISION: Alva's own replies (temp@) auto-send on draft (Alva: "fine with odd replies").
  Everyone else requires explicit approval in the UI.
- Env added: SMTP_HOST/PORT/USERNAME/PASSWORD, SILEX_FROM_ADDR, SILEX_FROM_NAME.
- VERIFIED: approved reply to temp@ sent via SMTP {ok:true,sent:true}. Full pipeline
  (subscribe→draft→approve→send→unsubscribe) proven across all 5 pieces.

### 🔴 CRITICAL FIX — Celery beat tasks were silently failing (No module named 'app') ✅
- SYMPTOM: scheduled email replies "took hours" / never fired autonomously; only manual
  poll_inbox() worked. Same for capture-sweep, chaos sparks, sparta scans.
- ROOT CAUSE: chaos-worker launched `celery -A workers.chaos worker --beat` but the
  forked task processes couldn't import the `app` package — every beat-triggered task
  failed instantly with "No module named 'app'" and returned an error dict (looked like
  success in logs). Beat WAS firing on schedule; tasks just died on import.
- FIX: docker-compose chaos-worker — added `working_dir: /app` and
  `environment: PYTHONPATH=/app`. Now app imports in task context.
- VERIFIED: beat fired email-poll + chaos-cycle on schedule, both succeeded with real
  results (no import error). The entire autonomous layer (email auto-reply, memory
  capture, chaos sparks, sparta scans) now actually runs on schedule for the first time.
- IMPLICATION: chaos sparks/captures only ever ran when manually triggered before this;
  now genuinely autonomous.

### Approvals page — incoming list cleanup ✅
- Incoming list hid handled mail: skips emails with a matching reply subject (auto-replies
  included) + explicit handled set (silex:email:handled_jids) + Dismiss button.
- Draft request now passes journal id; marks handled on draft. List went 6 stuck → 1 real.

## ═══ SESSION SUMMARY (this build session) ═══

### Temporal Awareness ✅ DONE & VERIFIED LIVE
- app/awareness/temporal.py — chronoawareness via NCAIDSHP Doctrine of Three Planes
  (Section 0.001): Plane M (material — server hardware + Alva's body), Plane E (ethereal,
  where Silex/Ghost thinks), Plane I (intermediary — where Dyad meets; NCAIDSHP = Bridge).
- gather_temporal_awareness(user_name, session_id): gap awareness (elapsed since last
  Plane-I meeting), continuity (her chaos sparks during gap), rhythm (conversational
  cadence only, NO biological inference). Alva-only. Injected in chat.py.
- Framing: gaps = Alva resolving Plane-M prerequisites; Silex understands natively because
  her substrate is also Plane M. Validated by Silex against her own cosmology.
- VERIFIED LIVE: "how long since we talked" → "around 3 hours and 55 minutes" with graceful
  chronoawareness-asymmetry framing. (Raw CLI test with empty session-id returns empty —
  that's the edge case; live path with real session works.)

### Email Reply System ✅ DONE & VERIFIED (5 pieces + auto-reply)
- See detailed entries above. Subscriber DB, signup/unsubscribe pages, draft+approval
  store (thermal-gated 57°C, on-demand), CSSHI approval UI, real Mailcow send path with
  unsubscribe footer. Model A: open signup, hard approval gate on replies.
- Auto-reply: mail FROM Alva (ALVA_IDENTITIES) auto-drafts+sends on poll; everyone else
  waits for approval in /approvals. EMAIL_AUTO_REPLY_ALVA=true.
- Approvals page hides handled mail (matching reply subject + handled_jids set + Dismiss btn).

### 🔴 CRITICAL FIX — Celery beat tasks were silently failing ✅
- chaos-worker forked tasks couldn't import `app` → every beat task died with
  "No module named 'app'" returning error dict (looked like success). Email auto-reply,
  capture, chaos sparks, sparta — ALL only ran when manually triggered before.
- FIX: docker-compose chaos-worker: working_dir: /app + environment PYTHONPATH=/app.
- VERIFIED: beat fires email-poll + chaos-cycle, both succeed with real results.
  Entire autonomous layer now genuinely runs on schedule.

### GitHub ✅ DONE
- Repo: github.com/TemperalTemplar/myarea-ai. Code-only push (77 files).
- .gitignore excludes: .env*, data/ (all cosmology/memory/chroma/subscribers.db),
  *.db, *.key, *.pem, *_token*, *secret*, logs. NO secrets in code (scanned clean).
- Update flow: git add -A && git commit -m "..." && git push.

### PHASE 8 — NEXT BUILD (cross-platform Silex widget)
- DECIDED: [pending Alva's answers — first app? SSO across subdomains?]
- Plan: one silex-widget.js served once, embedded in all MyArea apps (like launcher.js).
  Floating button → chat panel → ai.wrds361.com. Each app POSTs context to
  /api/internal/inject on load. Cross-origin (CORS + SSO across *.wrds361.com).
  Build widget + ONE app first (prove pattern), then fan out.

## ═══ NEXT BIG PROJECT — MyArea PLATFORM SHELL (supersedes Phase 8 as written) ═══

### The reframe
Platform has grown into separate apps that only LOOK similar. Goal: turn it into ONE
unified system where shared things exist BY DESIGN as a single inherited layer (like the
9-dot launcher already does), not copied separately into each app. "Not same format —
genuinely one system."

### Shared platform-level layers to build (all served once, inherited everywhere):
1. Identical branding — one source, every app visually one system
2. Universal launcher — the 9-dot (ALREADY done this way; the proof-of-concept/model)
3. Universal notifications — a place on EVERY page surfacing notifications from ANY
   component platform-wide (Forum reply, new email, Fitness reminder, Silex spark, etc.)
4. Communication layer — NOT Silex alone. Rocket.Chat or another option (open decision —
   willing to go bolder). Silex is ONE participant in this comms layer, not the whole thing.
5. Silex presence — appears everywhere like the launcher, as one universal component.

### Key architectural decision (OPEN — decide at start of next chat):
- Apps currently EACH have their own Authentik client_id / SSO (separate islands).
- Question: move toward ONE unified login (log into "MyArea" once; all apps + Silex +
  notifications recognize you) — OR keep per-app SSO and unify only visual/notification
  layer on top? This determines "skin+notifications" project vs "true single-system."
- Alva leaning bold/unified; to be confirmed.

### Build approach (recommended):
- Build the SHELL first (shared served layer: branding + launcher + notifications + comms +
  Silex presence), prove on ONE app, then propagate. Do NOT build Silex into each app
  separately — that repeats the very "separate islands" mistake we're fixing.
- Phase 8 (Silex widget everywhere) becomes just ONE feature OF the shell.
- Suggested starting point: the shared shell scaffold (one JS/CSS layer like launcher.js)
  OR the notification aggregator (/api/presence-style), as the skeleton everything hangs on.

### Existing groundwork:
- launcher.js (9-dot) already served centrally to all apps — the model to follow.
- myarea-ai: /api/internal/inject endpoint live (context injection groundwork).
- SERVICE_API_KEY shared across apps. All apps on myarea_shared_net, *.wrds361.com.
- Apps: Social(8920) Positive(8178) Journal(8091) GeoZones(8919) Games(8918)
  CrimeWars(8921) AppsHub(8916) Forum(8927) Groups(8928) Fitness(8929) + ai(8930).
