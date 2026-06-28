#!/usr/bin/env python3
"""Import claude.ai web export (conversations.json) into Cursor native chat sessions."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ms_ts(dt: datetime | None, fallback: datetime) -> int:
    d = dt if dt is not None else fallback
    return int(d.timestamp() * 1000)


def _iso_ts(dt: datetime | None, fallback: datetime) -> str:
    d = (dt if dt is not None else fallback).astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------------
# Conversation parsing helpers
# ---------------------------------------------------------------------------

def _leaf_ts(msg: dict) -> datetime:
    ts = _parse_ts(msg.get("created_at"))
    return ts if ts is not None else _MIN_DT


def _active_lineage(messages: list[dict]) -> list[dict]:
    if not messages:
        return []

    by_uuid = {m["uuid"]: m for m in messages if m.get("uuid")}
    children: dict[str, list[str]] = {}
    for m in messages:
        parent = m.get("parent_message_uuid")
        uid = m.get("uuid")
        if parent and uid:
            children.setdefault(parent, []).append(uid)

    leaves = [m for m in messages if m.get("uuid") and m["uuid"] not in children]
    if not leaves:
        return sorted(messages, key=_leaf_ts)

    leaf = max(leaves, key=_leaf_ts)
    lineage: list[dict] = []
    current: dict | None = leaf
    seen: set[str] = set()
    while current and current.get("uuid") not in seen:
        lineage.append(current)
        seen.add(current["uuid"])
        parent_id = current.get("parent_message_uuid")
        current = by_uuid.get(parent_id) if parent_id else None
    lineage.reverse()
    return lineage


def _blocks_to_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif btype == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                parts.append(f"<thinking>\n{thinking}\n</thinking>")
        elif btype == "tool_use":
            name = block.get("name", "unknown")
            parts.append(f"[tool call: {name}]")
        elif btype == "tool_result":
            content = block.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content)
            elif isinstance(content, list):
                parts.append(_blocks_to_text(content))
    return "\n\n".join(p for p in parts if p.strip())


def _message_text(msg: dict) -> str:
    content = msg.get("content") or []
    if content:
        return _blocks_to_text(content)
    return (msg.get("text") or "").strip()


# ---------------------------------------------------------------------------
# Cursor path resolution
# ---------------------------------------------------------------------------

def _cursor_user_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User"
    elif system == "Linux":
        return Path.home() / ".config" / "Cursor" / "User"
    elif system == "Windows":
        return Path(os.environ["APPDATA"]) / "Cursor" / "User"
    raise SystemExit(f"Unsupported platform: {system}")


def _find_workspaces(project_dir: Path) -> tuple[str, Path, list[Path]]:
    """Return (workspace_hash, global_db_path, workspace_db_paths) for a Cursor project directory.

    Returns all workspace DBs that match the target directory (Cursor may create multiple hashes
    for the same folder across different sessions).  The workspace_hash is taken from the most
    recently modified matching workspace.
    """
    user_dir = _cursor_user_dir()
    global_db = user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        raise SystemExit("Cursor globalStorage/state.vscdb not found. Is Cursor installed?")

    ws_root = user_dir / "workspaceStorage"
    if not ws_root.exists():
        raise SystemExit(
            "Cursor workspaceStorage directory not found. "
            "Open your project folder in Cursor at least once, quit Cursor, then retry."
        )
    target = project_dir.resolve()

    matches: list[tuple[float, str, Path]] = []  # (mtime, hash, ws_db)
    for ws_dir in ws_root.iterdir():
        wj = ws_dir / "workspace.json"
        if not wj.exists():
            continue
        try:
            data = json.loads(wj.read_text(encoding="utf-8"))
            folder_url = data.get("folder", "")
            parsed = urlparse(folder_url)
            if parsed.scheme != "file":
                continue
            raw_path = unquote(parsed.path)
            # On Windows, urlparse yields /C:/... — strip the leading slash.
            if platform.system() == "Windows" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
                raw_path = raw_path[1:]
            folder_path = Path(raw_path).resolve()
            if folder_path == target:
                ws_db = ws_dir / "state.vscdb"
                mtime = ws_db.stat().st_mtime if ws_db.exists() else 0.0
                matches.append((mtime, ws_dir.name, ws_db))
        except Exception:
            continue

    if not matches:
        raise SystemExit(
            f"No Cursor workspace found for {target}.\n"
            "Open the folder in Cursor at least once, quit Cursor, then retry."
        )

    matches.sort(reverse=True)  # most recently used first
    ws_hash = matches[0][1]
    ws_dbs = [m[2] for m in matches if m[2].exists()]
    if not ws_dbs:
        raise SystemExit(
            f"Cursor workspace.json found for {target} but state.vscdb is missing.\n"
            "Open the folder in Cursor at least once, quit Cursor, then retry."
        )
    return ws_hash, global_db, ws_dbs


# ---------------------------------------------------------------------------
# Lexical rich-text helpers
# ---------------------------------------------------------------------------

def _make_rich_text(text: str) -> str:
    node = {
        "detail": 0, "format": 0, "mode": "normal", "style": "",
        "text": text, "type": "text", "version": 1,
    }
    para = {
        "children": [node], "direction": "ltr", "format": "",
        "indent": 0, "type": "paragraph", "version": 1,
    }
    root = {
        "children": [para], "direction": "ltr", "format": "",
        "indent": 0, "type": "root", "version": 1,
    }
    return json.dumps({"root": root}, ensure_ascii=False)


def _empty_rich_text() -> str:
    para = {
        "children": [], "direction": None, "format": "",
        "indent": 0, "type": "paragraph", "version": 1,
    }
    root = {
        "children": [para], "direction": None, "format": "",
        "indent": 0, "type": "root", "version": 1,
    }
    return json.dumps({"root": root}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Minimal Cursor context structure
# ---------------------------------------------------------------------------

_MINIMAL_CONTEXT = {
    "composers": [], "selectedCommits": [], "selectedPullRequests": [],
    "selectedImages": [], "selectedDocuments": [], "selectedVideos": [],
    "folderSelections": [], "fileSelections": [], "selections": [],
    "terminalSelections": [], "terminalFiles": [], "selectedDocs": [], "externalLinks": [],
    "cursorRules": [], "cursorCommands": [], "gitPRDiffSelections": [],
    "subagentSelections": [], "browserSelections": [], "extraContext": [],
    "mentions": {
        "composers": {}, "selectedCommits": {}, "selectedPullRequests": {},
        "gitDiff": [], "gitDiffFromBranchToMain": [], "selectedImages": {},
        "folderSelections": {}, "fileSelections": {}, "terminalFiles": {},
        "selections": {}, "terminalSelections": {}, "selectedDocs": {},
        "externalLinks": {}, "cursorRules": {}, "cursorCommands": {},
        "uiElementSelections": [], "consoleLogs": [], "gitPRDiffSelections": {},
        "subagentSelections": {}, "browserSelections": {},
        "diffHistory": [], "ideEditorsState": [],
    },
}


# ---------------------------------------------------------------------------
# Build Cursor DB payloads from a raw conversation dict
# ---------------------------------------------------------------------------

def _compute_subtitle(raw: dict) -> str:
    """Compute the subtitle string for a raw conversation without building the full composer payload."""
    lineage = _active_lineage(raw.get("chat_messages") or [])
    first_user_text = ""
    for msg in lineage:
        if msg.get("sender") == "human":
            text = _message_text(msg)
            if text:
                first_user_text = text
                break
    first60 = first_user_text[:60]
    suffix = "…" if len(first_user_text) > 60 else ""
    return f"Imported from claude.ai: {first60}{suffix}"


def build_composer_data(
    raw: dict,
    composer_id: str,
    ws_hash: str,
    project_dir: Path,
) -> tuple[dict, list[tuple[str, str]], dict]:
    """Return (composer_data, bubble_kv_pairs, allcomposers_entry)."""
    name = (raw.get("name") or "Claude chat").strip() or "Claude chat"
    now = datetime.now(timezone.utc)
    created_dt = _parse_ts(raw.get("created_at"))
    updated_dt = _parse_ts(raw.get("updated_at")) or created_dt
    created_ms = _ms_ts(created_dt, now)
    updated_ms = _ms_ts(updated_dt, now)

    lineage = _active_lineage(raw.get("chat_messages") or [])

    headers: list[dict] = []
    bubbles: list[tuple[str, str]] = []
    first_user_text = ""

    for msg in lineage:
        sender = msg.get("sender")
        btype_int = 1 if sender == "human" else 2
        msg_dt = _parse_ts(msg.get("created_at")) or now
        bubble_id = str(uuid.uuid4())
        text = _message_text(msg)

        if not text:
            continue

        if sender == "human" and not first_user_text:
            first_user_text = text

        headers.append({
            "bubbleId": bubble_id,
            "type": btype_int,
            "grouping": {
                "isRenderable": True,
                "hasText": True,
                "isShortPlainText": btype_int == 1 and len(text) < 100,
            },
            "createdAt": _iso_ts(msg_dt, now),
        })

        if btype_int == 1:
            bubble: dict = {
                "_v": 3, "type": 1, "bubbleId": bubble_id,
                "isAgentic": False,
                "existedSubsequentTerminalCommand": False,
                "existedPreviousTerminalCommand": False,
                "attachedHumanChanges": False,
                "tokenCount": {"inputTokens": 0, "outputTokens": 0},
                "isRefunded": False, "unifiedMode": 2,
                "createdAt": _iso_ts(msg_dt, now),
                "conversationState": "~",
                "richText": _make_rich_text(text),
                "text": text,
                "context": _MINIMAL_CONTEXT,
            }
        else:
            bubble = {
                "_v": 3, "type": 2, "bubbleId": bubble_id,
                "isAgentic": False,
                "existedSubsequentTerminalCommand": False,
                "existedPreviousTerminalCommand": False,
                "tokenCount": {"inputTokens": 0, "outputTokens": 0},
                "isRefunded": False, "unifiedMode": 2,
                "createdAt": _iso_ts(msg_dt, now),
                "conversationState": "~",
                "text": text,
                "toolResults": [], "suggestedCodeBlocks": [],
                "relevantFiles": [], "capabilities": [],
                "capabilityContexts": [], "todos": [],
            }

        bubbles.append((
            f"bubbleId:{composer_id}:{bubble_id}",
            json.dumps(bubble, ensure_ascii=False),
        ))

    first60 = first_user_text[:60]
    suffix = "…" if len(first_user_text) > 60 else ""
    subtitle = f"Imported from claude.ai: {first60}{suffix}"

    ws_id_obj = {
        "id": ws_hash,
        "uri": {
            "$mid": 1,
            "fsPath": str(project_dir),
            "external": f"file://{project_dir}",
            "path": str(project_dir),
            "scheme": "file",
        },
    }

    composer_data = {
        "_v": 16,
        "composerId": composer_id,
        "richText": _empty_rich_text(),
        "hasLoaded": True,
        "text": "",
        "fullConversationHeadersOnly": headers,
        "conversationMap": {},
        "status": "completed",
        "context": _MINIMAL_CONTEXT,
        "generatingBubbleIds": [],
        "isReadingLongFile": False,
        "codeBlockData": {},
        "newlyCreatedFiles": [],
        "newlyCreatedFolders": [],
        "lastUpdatedAt": updated_ms,
        "conversationCheckpointLastUpdatedAt": updated_ms,
        "createdAt": created_ms,
        "hasChangedContext": False,
        "capabilities": [],
        "name": name,
        "isFileListExpanded": False,
        "unifiedMode": "agent",
        "forceMode": "edit",
        "usageData": {},
        "allAttachedFileCodeChunksUris": [],
        "subComposerIds": [],
        "capabilityContexts": [],
        "todos": [],
        "hasUnreadMessages": False,
        "isAgentic": False,
        "workspaceIdentifier": ws_id_obj,
        "subtitle": subtitle,
        "filesChangedCount": 0,
        "totalLinesAdded": 0,
        "totalLinesRemoved": 0,
        # Fields required by Cursor 3.9+ to include the session in the sidebar
        "isDraft": False,
        "isSpec": False,
        "isProject": False,
        "isBestOfNSubcomposer": False,
        "isBestOfNParent": False,
        "isWorktree": False,
        "worktreeStartedReadOnly": False,
        "isCreatingWorktree": False,
        "isApplyingWorktree": False,
        "isUndoingWorktree": False,
        "pendingCreateWorktree": False,
        "applied": False,
        "isNAL": False,
        "isSpecSubagentDone": False,
        "isContinuationInProgress": False,
        "isQueueExpanded": False,
        "activeTabsShouldBeReactive": False,
        "canvasPillCollapsed": False,
        "browserChipManuallyDisabled": False,
        "browserChipManuallyEnabled": False,
        "gitHubPromptDismissed": False,
        "planModeSuggestionUsed": False,
        "debugModeSuggestionUsed": False,
        "restrictAgentModeSwitching": False,
        "applyAgentBackendTypeRestrictions": False,
        "stopHookLoopCount": 0,
        "contextUsagePercent": 0,
        "contextTokensUsed": 0,
        "contextTokenLimit": 0,
        "latestChatGenerationUUID": "",
        "speculativeSummarizationEncryptionKey": "",
        "blobEncryptionKey": "",
        "agentBackend": "",
        "conversationState": "~",
        "originalFileStates": {},
        "addedFiles": 0,
        "removedFiles": 0,
        "queueItems": [],
        "subagentComposerIds": [],
        "trackedGitRepos": [],
        "promptTokenBreakdown": {},
        "promptContextUsageTree": {},
        "modelConfig": {},
        # Private fields used for legacy-import dedup detection (not read by Cursor).
        "_claudeSourceUuid": raw.get("uuid") or "",
        "_claudeSourcePath": str(project_dir.resolve()),
    }

    allcomposers_entry = {
        "type": "head",
        "composerId": composer_id,
        "name": name,
        "lastUpdatedAt": updated_ms,
        "conversationCheckpointLastUpdatedAt": updated_ms,
        "createdAt": created_ms,
        "unifiedMode": "agent",
        "forceMode": "edit",
        "hasUnreadMessages": False,
        "contextUsagePercent": 0,
        "totalLinesAdded": 0,
        "totalLinesRemoved": 0,
        "filesChangedCount": 0,
        "subtitle": subtitle,
        "hasBlockingPendingActions": False,
        "hasPendingPlan": False,
        "isArchived": False,
        "isDraft": False,
        "isWorktree": False,
        "worktreeStartedReadOnly": False,
        "isSpec": False,
        "isProject": False,
        "isBestOfNSubcomposer": False,
        "numSubComposers": 0,
        "referencedPlans": [],
        "trackedGitRepos": [],
        "workspaceIdentifier": ws_id_obj,
    }

    return composer_data, bubbles, allcomposers_entry


# ---------------------------------------------------------------------------
# SQLite write
# ---------------------------------------------------------------------------

def write_to_cursor(conversations: list[dict], project_dir: Path) -> tuple[int, int]:
    ws_hash, global_db, ws_dbs = _find_workspaces(project_dir)
    if len(ws_dbs) > 1:
        print(f"Note: {len(ws_dbs)} workspace DBs found for this folder; registering in all of them.")

    # Dedup key scoped to canonical project path — stable across Cursor re-indexing (new ws_hash).
    canonical_path = str(project_dir.resolve())

    # Build subtitle→composer_id index from existing workspace composerHeaders.
    # This catches imports that predate the claudeSourceId key mechanism (no dedup key was written).
    existing_by_subtitle: dict[str, str] = {}
    for ws_db in ws_dbs:
        try:
            with sqlite3.connect(str(ws_db), timeout=10) as wc:
                row = wc.execute(
                    "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
                ).fetchone()
                if row:
                    for entry in json.loads(row[0]).get("allComposers", []):
                        st = entry.get("subtitle") or ""
                        cid = entry.get("composerId")
                        if st.startswith("Imported from claude.ai:") and cid and st not in existing_by_subtitle:
                            existing_by_subtitle[st] = cid
        except Exception:
            pass

    phase1_fail = 0
    staged: list[tuple[str, str, str, dict, str]] = []  # (source_key, pending_key, composer_id, allcomposers_entry, name)

    with sqlite3.connect(str(global_db), timeout=10) as conn:
        for raw in conversations:
            name = (raw.get("name") or "Claude chat").strip()
            source_uuid = raw.get("uuid") or ""
            # When uuid is missing, fall back to a subtitle-based dedup key so conversations
            # without a uuid are still deduplicated and overwrite rather than duplicate.
            if source_uuid:
                source_key  = f"claudeSourceId:{canonical_path}:{source_uuid}"
                pending_key = f"claudePending:{canonical_path}:{source_uuid}"
            else:
                fallback = _compute_subtitle(raw)
                source_key  = f"claudeSourceId:{canonical_path}:subtitle:{fallback}" if fallback else ""
                pending_key = f"claudePending:{canonical_path}:subtitle:{fallback}" if fallback else ""
            composer_id = str(uuid.uuid4())
            try:
                # Overwrite: recover the existing composer_id so all downstream writes reuse
                # the same ID (idempotent workspace updates, no new header entry).
                # Check 1: claudeSourceId key (written by current code on successful import).
                existing_cid: str | None = None
                if source_key:
                    row = conn.execute(
                        "SELECT value FROM cursorDiskKV WHERE key=?", (source_key,)
                    ).fetchone()
                    if row:
                        existing_cid = row[0]

                # Check 2: subtitle index (catches imports that predate the claudeSourceId key).
                if existing_cid is None:
                    expected_subtitle = _compute_subtitle(raw)
                    if expected_subtitle in existing_by_subtitle:
                        existing_cid = existing_by_subtitle[expected_subtitle]

                if existing_cid is not None:
                    composer_id = existing_cid
                    print(f"OVERWRITE {name!r}  (reusing composer {composer_id})")

                # Partially done (workspace update failed on a previous run) — recover the
                # existing composer_id so workspace updates stay idempotent and no orphan is created.
                recovered = False
                allcomposers_entry: dict = {}
                if pending_key:
                    row = conn.execute(
                        "SELECT value FROM cursorDiskKV WHERE key=?", (pending_key,)
                    ).fetchone()
                    if row:
                        try:
                            pd = json.loads(row[0])
                            composer_id = pd["composer_id"]
                            allcomposers_entry = pd["allcomposers_entry"]
                            recovered = True
                            print(f"RESUME {name!r}  (reusing staged composer {composer_id})")
                        except Exception:
                            pass  # corrupt pending entry — fall through to fresh import

                if not recovered:
                    composer_data, bubbles, allcomposers_entry = build_composer_data(
                        raw, composer_id, ws_hash, project_dir
                    )

                    # Phase 1: write composerData + bubbles + pending marker atomically.
                    # global composer.composerHeaders is NOT written here — deferred to phase 2b
                    # so a workspace failure leaves no orphaned header entry.
                    conn.execute("BEGIN EXCLUSIVE")

                    conn.execute(
                        "INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES (?, ?)",
                        (f"composerData:{composer_id}", json.dumps(composer_data, ensure_ascii=False)),
                    )
                    for bkey, bval in bubbles:
                        conn.execute(
                            "INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES (?, ?)",
                            (bkey, bval),
                        )
                    if pending_key:
                        conn.execute(
                            "INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES (?, ?)",
                            (pending_key, json.dumps(
                                {"composer_id": composer_id, "allcomposers_entry": allcomposers_entry},
                                ensure_ascii=False,
                            )),
                        )
                    conn.execute("COMMIT")
                    print(f"STAGED  {name!r}  -> composer {composer_id}")

                staged.append((source_key, pending_key, composer_id, allcomposers_entry, name))

            except sqlite3.OperationalError as e:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                if "locked" in str(e).lower():
                    print(f"FAIL  {name!r}: database is locked — quit Cursor fully and retry", file=sys.stderr)
                else:
                    print(f"FAIL  {name!r}: {e}", file=sys.stderr)
                phase1_fail += 1
            except Exception as e:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                print(f"FAIL  {name!r}: {e}", file=sys.stderr)
                phase1_fail += 1

    if not staged:
        return 0, phase1_fail

    new_ids = [cid for _, _, cid, _, _ in staged]
    new_allcomposers_entries = [entry for _, _, _, entry, _ in staged]
    new_set = set(new_ids)

    # Phase 2a: update every workspace sidebar index.
    # Using the same composer IDs on retry makes these writes idempotent:
    # existing_ws_ids check prevents duplicate header entries; new_set filter deduplicates selectedComposerIds.
    ws_fail = 0
    for ws_db in ws_dbs:
        try:
            with sqlite3.connect(str(ws_db), timeout=10) as ws_conn:
                ws_conn.execute("BEGIN EXCLUSIVE")

                row = ws_conn.execute(
                    "SELECT value FROM ItemTable WHERE key='composer.composerData'"
                ).fetchone()
                if row:
                    cd = json.loads(row[0])
                else:
                    # New workspace: seed required migration flags so Cursor recognises the record.
                    cd = {
                        "hasMigratedComposerData": True,
                        "hasMigratedMultipleComposers": True,
                        "selectedComposerIds": [],
                        "lastFocusedComposerIds": [],
                    }
                existing_sel = cd.get("selectedComposerIds") or []
                cd["selectedComposerIds"] = new_ids + [i for i in existing_sel if i not in new_set]
                lf = cd.get("lastFocusedComposerIds") or []
                cd["lastFocusedComposerIds"] = new_ids + [i for i in lf if i not in new_set]
                ws_conn.execute(
                    "INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)",
                    ("composer.composerData", json.dumps(cd, ensure_ascii=False)),
                )

                # Cursor 3.9.x reads composer.composerHeaders from the workspace DB after migration.
                row2 = ws_conn.execute(
                    "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
                ).fetchone()
                ws_headers = json.loads(row2[0]) if row2 else {"allComposers": []}
                existing_ws_ids = {c.get("composerId") for c in ws_headers.get("allComposers", [])}
                for entry in reversed(new_allcomposers_entries):
                    if entry.get("composerId") not in existing_ws_ids:
                        ws_headers.setdefault("allComposers", []).insert(0, entry)
                ws_conn.execute(
                    "INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)",
                    ("composer.composerHeaders", json.dumps(ws_headers, ensure_ascii=False)),
                )

                ws_conn.execute("COMMIT")
        except Exception as e:
            print(f"Warning: could not update workspace sidebar index ({ws_db.parent.name}): {e}", file=sys.stderr)
            ws_fail += 1

    if ws_fail:
        print(
            f"\nWarning: {ws_fail} workspace sidebar index update(s) failed — "
            "sessions are staged in the global DB. "
            "Ensure Cursor is fully quit and retry; the same session IDs will be reused automatically.",
            file=sys.stderr,
        )
        return 0, phase1_fail + len(staged)

    # Phase 2b: all workspace updates succeeded — commit global composer.composerHeaders,
    # dedup markers, and clean up pending markers atomically.
    with sqlite3.connect(str(global_db), timeout=10) as conn:
        conn.execute("BEGIN EXCLUSIVE")

        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
        ).fetchone()
        headers_data = json.loads(row[0]) if row else {"allComposers": []}
        existing_global_ids = {c.get("composerId") for c in headers_data.get("allComposers", [])}
        for entry in reversed(new_allcomposers_entries):
            if entry.get("composerId") not in existing_global_ids:
                headers_data["allComposers"].insert(0, entry)
        conn.execute(
            "INSERT OR REPLACE INTO ItemTable(key, value) VALUES (?, ?)",
            ("composer.composerHeaders", json.dumps(headers_data, ensure_ascii=False)),
        )

        for source_key, pending_key, composer_id, _, _ in staged:
            if source_key:
                conn.execute(
                    "INSERT OR REPLACE INTO cursorDiskKV(key, value) VALUES (?, ?)",
                    (source_key, composer_id),
                )
            if pending_key:
                conn.execute("DELETE FROM cursorDiskKV WHERE key=?", (pending_key,))

        conn.execute("COMMIT")

    for _, _, composer_id, _, name in staged:
        print(f"OK  {name!r}  -> Cursor composer {composer_id}")

    return len(staged), phase1_fail


# ---------------------------------------------------------------------------
# Export loading
# ---------------------------------------------------------------------------

def load_export(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"Could not parse {path}: {e}\n"
            "Re-export your data from claude.ai → Settings → Privacy → Export data."
        )
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "conversations" in data:
        return data["conversations"]
    raise SystemExit(f"Unsupported export format in {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Import claude.ai web export into Cursor sessions")
    parser.add_argument("export_file", type=Path, help="Path to conversations.json")
    parser.add_argument("--dir", type=Path, default=None, help="Absolute path of the Cursor project directory (required unless --list)")
    parser.add_argument("--list", action="store_true", help="List conversations and exit")
    parser.add_argument("--id", action="append", dest="ids", help="Import conversation UUID (repeatable)")
    parser.add_argument("--all", action="store_true", help="Import every conversation")
    args = parser.parse_args()

    if not args.export_file.exists():
        raise SystemExit(f"File not found: {args.export_file}")

    conversations = load_export(args.export_file)

    if args.list:
        for i, c in enumerate(conversations, 1):
            uid = c.get("uuid", "?")
            name = (c.get("name") or "Untitled").strip()
            updated = (c.get("updated_at") or "")[:10]
            n = len(c.get("chat_messages") or [])
            print(f"{i:4d}  {updated}  {uid}  {name}  ({n} msgs)")
        return

    if not args.all and not args.ids:
        raise SystemExit("Specify --list, --all, or --id <uuid>")

    if args.dir is None:
        raise SystemExit("--dir is required: provide the absolute path of the Cursor project directory.")
    project_dir = args.dir.resolve()
    selected = conversations if args.all else [c for c in conversations if c.get("uuid") in set(args.ids)]
    if args.ids and len(selected) != len(args.ids):
        found = {c.get("uuid") for c in selected}
        missing = [i for i in args.ids if i not in found]
        print(f"Warning: UUID(s) not found: {', '.join(missing)}", file=sys.stderr)

    if not selected:
        raise SystemExit("No matching conversations found.")

    print(f"Target project: {project_dir}")
    print("Tip: quit Cursor before import to avoid DB conflicts.\n")

    ok, fail = write_to_cursor(selected, project_dir)

    print(f"\nDone: {ok} imported, {fail} failed.")
    if ok:
        print("Reopen Cursor and check the chat sidebar for imported sessions.")


if __name__ == "__main__":
    main()
