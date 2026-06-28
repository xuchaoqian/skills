# skills

Personal [Agent Skills](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills) for Claude and Cursor.

## Skills

| Skill | Description |
|-------|-------------|
| [claude-web-to-cursor](./claude-web-to-cursor/) | Import [claude.ai](https://claude.ai) chat history into Cursor native sidebar sessions |

---

## claude-web-to-cursor

Import Anthropic's `conversations.json` export into **Cursor's chat sidebar** (Composer / Agent sessions). This writes Cursor's local SQLite database — it is not Markdown `@` file context.

**Supported:** claude.ai web → Cursor native sessions  
**Not supported:** Markdown-only context, Claude Code terminal history, or claude.ai ↔ Claude Code sync

Verified against Cursor 3.9.8; future Cursor schema changes may require script updates.

### Install

Copy the `claude-web-to-cursor/` folder into your project or global skills directory:

```bash
# From this repo — project-local
mkdir -p .claude/skills
cp -r /path/to/skills/claude-web-to-cursor .claude/skills/
chmod +x .claude/skills/claude-web-to-cursor/scripts/claude_web_to_cursor.py
```

```bash
# Global (Claude Code)
cp -r claude-web-to-cursor ~/.claude/skills/
chmod +x ~/.claude/skills/claude-web-to-cursor/scripts/claude_web_to_cursor.py
```

Optional — register for **Cursor Agent**:

```bash
ln -sf "$(pwd)/claude-web-to-cursor" ~/.cursor/skills/claude-web-to-cursor
```

Requirements: Python 3.9+, stdlib only (no pip install).

Optional: `pip install html-to-markdown` (Python ≥3.10) for higher-fidelity
HTML→Markdown of widgets/artifacts. When absent, a built-in stdlib converter is
used instead — no install required.

### Export from claude.ai

1. Go to [claude.ai](https://claude.ai) → **Settings** → **Privacy** → **Export data**
2. Download and unzip the archive
3. Locate `conversations.json`

```bash
unzip ~/Downloads/claude_export_*.zip -d /tmp/claude_export
ls /tmp/claude_export/conversations.json
```

### Usage

Set `SCRIPT` to the path of `scripts/claude_web_to_cursor.py` (adjust if installed elsewhere):

```bash
SCRIPT="claude-web-to-cursor/scripts/claude_web_to_cursor.py"
EXPORT="/tmp/claude_export/conversations.json"
PROJECT="/absolute/path/to/your/cursor/project"   # must match the folder opened in Cursor
```

**Before importing:** open the target project in Cursor at least once, then **fully quit Cursor** (Cmd+Q on macOS). Back up `state.vscdb` first (see [Backup](#backup-cursor-database)).

#### List conversations

```bash
python3 "$SCRIPT" --list "$EXPORT"
```

#### Import one conversation

```bash
python3 "$SCRIPT" \
  --id <UUID-from-list> \
  --dir "$PROJECT" \
  "$EXPORT"
```

Repeat `--id` to import multiple chats:

```bash
python3 "$SCRIPT" \
  --id <UUID-1> \
  --id <UUID-2> \
  --dir "$PROJECT" \
  "$EXPORT"
```

#### Import all conversations

```bash
python3 "$SCRIPT" \
  --all \
  --dir "$PROJECT" \
  "$EXPORT"
```

#### Verify

Reopen Cursor, open the same project folder (`--dir`), and check the Chat / Composer sidebar.

### Backup Cursor database

**macOS:**

```bash
cp "$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb" \
   "$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb.bak.$(date +%Y%m%d)"
```

**Linux:** `~/.config/Cursor/User/globalStorage/state.vscdb`  
**Windows:** `%APPDATA%\Cursor\User\globalStorage\state.vscdb`

### Limitations

- Imports the active conversation branch only (edit/regenerate branches are dropped)
- `tool_use` blocks carrying HTML (e.g. `visualize:show_widget` tables/artifacts) are converted to Markdown and render natively in Cursor; tool calls without an HTML payload (e.g. `web_search`) show a `[label]` badge, and non-convertible widgets (SVG/charts) degrade to their visible text
- Artifacts UI, images, and attachments may be incomplete
- Claude Project knowledge must be migrated manually to `.cursor/rules/`

### Troubleshooting

| Error | Fix |
|-------|-----|
| `No Cursor workspace found` | Open the `--dir` folder in Cursor once, quit, retry |
| `database is locked` | Quit Cursor completely, then retry |
| Sidebar empty after import | Ensure `--dir` exactly matches the folder opened in Cursor |

See [claude-web-to-cursor/reference.md](./claude-web-to-cursor/reference.md) for storage layout and error details.

### Using with an agent

In Claude Code or Cursor, mention migrating Claude web chats to Cursor — the agent can run the script for you. Provide:

1. Path to `conversations.json`
2. Absolute path of the target Cursor project
3. Which chats to import (`--all`, or specific UUIDs from `--list`)

The agent skill lives at [claude-web-to-cursor/SKILL.md](./claude-web-to-cursor/SKILL.md).
