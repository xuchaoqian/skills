---
name: claude-web-to-cursor
description: >-
  Import claude.ai web chat history into Cursor native chat sessions (sidebar
  composers, not @-file context). Use when the user wants to migrate, import,
  transfer, or restore Claude web conversations into Cursor, export
  conversations.json from claude.ai, or move chat history between Claude and
  Cursor.
disable-model-invocation: true
---

# Claude Web → Cursor Native Sessions

Import [claude.ai](https://claude.ai) `conversations.json` into **Cursor's chat sidebar**. This is not Markdown `@` context — it writes Cursor's local SQLite (`state.vscdb`) using a compatible subset of Cursor's internal format (verified against Cursor 3.9.8 local DB; future schema changes may require updates).

Skill root (this file's directory):

```text
.claude/skills/claude-web-to-cursor/
├── SKILL.md
├── reference.md
└── scripts/
    ├── claude_web_to_cursor.py
    └── html_to_md.py          # HTML→Markdown converter (widgets/artifacts)
```

Resolve paths from skill location:

```text
SKILL_DIR = directory containing this SKILL.md
SCRIPT    = $SKILL_DIR/scripts/claude_web_to_cursor.py
```

## Scope

| Goal | This skill |
|------|------------|
| claude.ai → Cursor native sessions | ✅ |
| claude.ai → Markdown `@` context only | ❌ browser export + project docs |
| Claude Code terminal → Cursor | ❌ separate tooling required |
| claude.ai ↔ Claude Code sync | ❌ not supported by Anthropic |

## Before starting — collect from user

1. **Export file**: path to `conversations.json` (Anthropic ZIP export)
2. **Target project**: absolute path of the Cursor project folder the conversations should appear in (must match exactly what Cursor opened). **Ask the user explicitly — never infer from cwd or skill location.**
3. **Selection**: `--all`, `--id <UUID>`, or `--list` first
4. `--dir` is **required** for all import commands (not needed for `--list`)
5. User **quit Cursor completely** before import

Export guide if missing: claude.ai → Settings → Privacy → **Export data** → unzip `conversations.json`.

## One-time setup

No virtual environment needed — stdlib only (Python 3.9+).

**Optional fidelity upgrade:** `pip install html-to-markdown` (Python ≥3.10).
When present, `scripts/html_to_md.py` uses it for higher-fidelity HTML→Markdown
(full CommonMark, nested tables, colspan/rowspan); otherwise it falls back to
the built-in stdlib converter. Not required — the fallback handles Claude's
widget HTML well.

**Project-local install** (inside a repo):

```bash
SKILL_DIR=".claude/skills/claude-web-to-cursor"
chmod +x "$SKILL_DIR/scripts/claude_web_to_cursor.py"
```

**Global install** (`~/.claude/skills/`):

```bash
SKILL_DIR="$HOME/.claude/skills/claude-web-to-cursor"
chmod +x "$SKILL_DIR/scripts/claude_web_to_cursor.py"
```

## Migration workflow

```text
- [ ] conversations.json extracted from Anthropic ZIP
- [ ] Target project opened in Cursor at least once
- [ ] Cursor fully quit
- [ ] state.vscdb backed up (recommended)
- [ ] --list reviewed (if picking specific chats)
- [ ] Import run
- [ ] Cursor reopened; sidebar verified
```

### Step 0 — Extract Anthropic export

```bash
# After downloading from claude.ai → Settings → Privacy → Export data:
unzip ~/Downloads/claude_export_*.zip -d /tmp/claude_export
ls /tmp/claude_export/   # should show conversations.json, users.json, etc.
```

### Step 1 — Backup Cursor DB

**macOS:**

```bash
cp "$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb" \
   "$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb.bak.$(date +%Y%m%d)"
```

**Linux:** `~/.config/Cursor/User/globalStorage/state.vscdb`  
**Windows:** `%APPDATA%\Cursor\User\globalStorage\state.vscdb`

### Step 2 — List conversations

```bash
SKILL_DIR=".claude/skills/claude-web-to-cursor"
python3 "$SKILL_DIR/scripts/claude_web_to_cursor.py" --list /tmp/claude_export/conversations.json
```

### Step 3 — Import

```bash
SKILL_DIR=".claude/skills/claude-web-to-cursor"
python3 "$SKILL_DIR/scripts/claude_web_to_cursor.py" \
  --id <UUID> \
  --dir /absolute/path/to/cursor/project \
  /tmp/claude_export/conversations.json
```

Batch: replace `--id <UUID>` with `--all`.

### Step 4 — Verify

Open Cursor → same `--dir` project → check Chat/Composer sidebar.

## Agent behavior

- Resolve `SKILL_DIR` from this skill's path (`.claude/skills/claude-web-to-cursor`).
- **Execute** `scripts/claude_web_to_cursor.py`; do not reimplement DB writes.
- **Show the script's full stdout to the user verbatim** — do not summarize it
  away. It includes the active `HTML→Markdown backend:` line (library vs. stdlib
  fallback) and per-conversation `OK`/`FAIL` results the user needs to see.
- When reporting the import result, you **must** state which HTML→Markdown
  backend ran. It is on the final `Done: … (HTML→Markdown backend: …)` line — quote
  it. Never report success without naming the backend.
- Use absolute paths for `--dir` and export file.
- On `No Cursor workspace found`: open folder in Cursor once, quit, retry.
- On `database is locked`: user must quit Cursor fully.
- Never delete `state.vscdb` without backup.

## Limitations

- Active conversation branch only (edit/regenerate branches dropped)
- `tool_use` blocks carrying HTML (e.g. `visualize:show_widget` tables/artifacts) are converted to Markdown so Cursor renders them natively (converter in `scripts/html_to_md.py`); tables become GFM tables (multi-line cells use `<br>`). Tool calls with no HTML payload (e.g. `web_search`) still show a `[label]` badge, and non-convertible widgets (SVG/charts/interactive UI) degrade to their visible text
- Artifacts UI, images, attachments may be incomplete
- Claude Project knowledge → manual `.cursor/rules/` migration
- Requires Python 3.9+ (no pip install needed)
- Writes the legacy `ItemTable`-based index; if Cursor migrates to its new `composerHeaders` table in a future release, a script update will be needed

## Alternative: context-only (not native sessions)

Claude Exporter → `.md` → `.cursor/claude-history/` → `@file` in Cursor chat.

## Troubleshooting

See [reference.md](reference.md).
