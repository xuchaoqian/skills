# Claude Web → Cursor — Reference

## Cursor storage layout

| Store | Path (macOS) | Holds |
|-------|--------------|-------|
| Global DB | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | `composerData:*` and `bubbleId:*` in `cursorDiskKV`; `composer.composerHeaders` index in `ItemTable` |
| Workspace DB | `.../workspaceStorage/<hash>/state.vscdb` | Open-tab composer IDs (`composer.composerData`); `composer.composerHeaders` index (Cursor 3.9.x reads this after migration) |
| Workspace map | `.../workspaceStorage/<hash>/workspace.json` | `{ "folder": "file:///..." }` — used to match `--dir` |

Linux: `~/.config/Cursor/...`  
Windows: `%APPDATA%\Cursor\...`

## Anthropic export format

`conversations.json` — JSON array of conversations with `chat_messages[]`.

Message `content[]` block types: `text`, `thinking`, `tool_use`, `tool_result`.

## Error reference

| Error | Cause | Fix |
|-------|-------|-----|
| `No Cursor workspace found` | `--dir` never opened in Cursor | Open folder in Cursor, quit, retry |
| `state.vscdb not found` | Cursor not installed | Install Cursor |
| `database is locked` | Cursor running | Quit fully (Cmd+Q on macOS), retry |
| Sidebar empty after import | Path mismatch | Match `--dir` exactly to the folder opened in Cursor |

## Install on another machine

Copy `.claude/skills/claude-web-to-cursor/` into any repo or `~/.claude/skills/`, then:

```bash
chmod +x .claude/skills/claude-web-to-cursor/scripts/claude-web-to-cursor.py
```

No pip install required — stdlib only (Python 3.9+).

Optional — also register for Cursor Agent:

```bash
SKILL_DIR="$(pwd)/.claude/skills/claude-web-to-cursor"
ln -sf "$SKILL_DIR" ~/.cursor/skills/claude-web-to-cursor
```

## Related tools

| Tool                                                  | Use                                       |
|-------------------------------------------------------|-------------------------------------------|
| Claude Exporter (Chrome)                              | Single chat → Markdown                    |
| [memex-chats](https://pypi.org/project/memex-chats/) | MCP search across claude.ai + Claude Code |
