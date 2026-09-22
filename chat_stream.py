# Packages/Agentic/chat_stream.py
# -------------------------------------------------------------
#  Agentic AI plugin: four primary commands
#
#  * AI Agent        - Execute an arbitrary prompt providing a code
#                       snippet or file
#
#  * AI Agent Action - Execute user-defined custom agent action
#                       on a custom code section.
#
#  * AI Agent Model Chat  - Start a chat with a specific model,
#                            optionally with a code snippet
#
#  * AI Agent Chat   - Stream on an existing chat view that already
#                       contains the tags "# --- System ---",
#                       "# --- User ---", "# --- Agent ---".
#                       Run with [ctrl/cmd] + [enter] in a chat file.
#
#  Supplemental chat management commands
#
#  * AI Agent Clear Reasoning  - Erase reasoning output from a chat
#
#  * AI Agent Clone Chat  - Create a copy of the current chat
#
#  * AI Agent New Chat    - Prepare a new chat file
#
#  * AI Agent Set Model   - Set the model for a chat file
#
#  * AI Agent Insert File - Insert a file into the chat
#
#  All API call logic lives in `chat_stream()` - the single code
#  path used by all three commands.
#
#  The plugin uses only Python features available in older
#  Sublime Text builds (no f-string syntax, only .format()).
# -------------------------------------------------------------
import os
import time
import json
import urllib.request
import threading
import random
import re

import sublime
import sublime_plugin

# Imports
try:
    from .lib.file_injector import process_messages_file_mentions, resolve_path
    from .lib.patch_parser import parse_patch_blocks
    from .lib.patch_engine import apply_patch_blocks
except (ImportError, ValueError):
    from lib.file_injector import process_messages_file_mentions, resolve_path
    from lib.patch_parser import parse_patch_blocks
    from lib.patch_engine import apply_patch_blocks

# Safer lower bound for code + English
CHARS_PER_TOKEN = 3.5  

# Registry of ongoing streams: view.id() -> StreamingTask
_ACTIVE_STREAMERS = {}

# Tags for chat file
TAG_MAP = {
    "developer": "# --- System ---",
    "user": "# --- User ---",
    "assistant": "# --- Agent ---",
}

_LAST_SANITIZE_DICT_RAW = None
_SANITIZE_DICT = None
_SANITIZE_RE = None
_LAST_MODEL_IDX = {}

SLASH_CMD_RE = re.compile(
    r"^\s*/(?P<cmd>edit|create)\s+(?:@?\"(?P<quoted>[^\"]+)\"|@?(?P<unquoted>\S+))(?:\s+(?P<prompt>[\s\S]*))?$",
    re.IGNORECASE
)

def _printstatus(msg):
    sublime.status_message(msg)
    if sublime.load_settings("Agentic.sublime-settings").get("console_log", False):
        print(msg)

def _pick_model(capability=None):
    """Return a random model from the provided model list string"""
    settings = sublime.load_settings("Agentic.sublime-settings")
    if capability is None:
        capability = settings.get("default_models")
    capable_models = settings.get(capability)
    models = settings.get("models")

    # round robin request load balancing
    if capability not in _LAST_MODEL_IDX:
        idx = 0
    else:
        idx = _LAST_MODEL_IDX[capability]
    model = capable_models[idx % len(capable_models)]
    _LAST_MODEL_IDX[capability] = (idx + 1) % len(capable_models)

    return model


def _parse_metrics(r):
    """
    Return (cache_n, prompt_n, prompt_per_sec, predicted_per_sec)
    from either a "timings" block or the older "usage" block.
    """
    t = r.get("timings")
    if t:
        return (t["cache_n"], t["prompt_n"],
                t["prompt_per_second"], t["predicted_per_second"])
    u = r.get("usage")  # groq / openai
    if u:
        cache = u.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        prompt = u.get("prompt_tokens", 0) - cache
        pt = u.get("prompt_time", 0) or 1e12
        ct = u.get("completion_time", 0) or 1e12
        return (cache, prompt,
                u.get("prompt_tokens", 0) / pt,
                u.get("completion_tokens", 0) / ct)
    return None

def _extract_slash_command(messages):
    """
    Checks if the last user message begins with a slash command.
    Returns (cmd_name, target_file, prompt) or (None, None, None).
    """
    if not messages:
        return None, None, None

    last_user_msg = None
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "").strip()
            break

    if not last_user_msg:
        return None, None, None

    match = SLASH_CMD_RE.match(last_user_msg)
    if not match:
        return None, None, None

    cmd = match.group("cmd").lower()
    filepath = match.group("quoted") or match.group("unquoted")
    prompt = (match.group("prompt") or "").strip()
    return cmd, filepath, prompt

def chat_stream(messages, model, cancel=None):
    """
    Query an OpenAI server given messages and a model configuration.
    Yields `(is_reasoning, text)` for incremental stream chunks.
    At the end yields a 4-tuple of timing metrics:
        (cache_n, prompt_n, prompt_per_second, predicted_per_second).
    """
    url = model.get("url")
    token = model.get("token")
    body = dict(model.get("options", {}))
    body.update({
        "messages": messages,
        "model": model.get("model"),
    })

    # some local models (EXAONE) do not support "developer"
    if model.get("no_system_prompt", False):
        for m in body.get("messages"):
            if m['role'] == "developer":
                m['role'] = "user"

    if "include_reasoning" in body and body["include_reasoning"]:
        body.update({  # match the package setting if True
            "include_reasoning":
            sublime.load_settings("Agentic.sublime-settings").get("show_reasoning")
        })

    stream = body.get("stream", False)

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(token),
            "User-Agent": "AsyncAPIClient/Python 2.14.0"
        },
    )

    if not stream:
        resp = urllib.request.urlopen(req)
        resp = json.loads(''.join([r.decode("utf-8") for r in resp]))
        m = resp["choices"][0]["message"]
        if "reasoning_content" in m and m["reasoning_content"]:
            yield (True, m["reasoning_content"])
        elif "reasoning" in m and m["reasoning"]:  # groq
            yield (True, m["reasoning"])
        if "content" in m and m["content"]:
            yield (False, m["content"])
        t = _parse_metrics(resp)
        if t:
            yield (t)
        return

    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            if cancel and cancel.is_set():
                return
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                return

            try:
                evt = json.loads(payload)
            except ValueError:
                continue

            # print(evt)  # debugging provider stream outputs
            for choice in evt.get("choices", []):
                delta = choice.get("delta", {})

                # Harvest content / reasoning tokens
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    yield (True, delta["reasoning_content"])
                elif "reasoning" in delta and delta["reasoning"]:  # groq
                    yield (True, delta["reasoning"])
                elif "content" in delta and delta["content"]:
                    yield (False, delta["content"])
                elif not delta:  # Final timing report
                    t = _parse_metrics(evt)
                    if t:
                        yield (t)


class AgentStreamingTask(threading.Thread):
    """Background worker - streams into the view"""
    def __init__(self, view, messages, registry):
        super().__init__(daemon=True)
        self.view = view
        self.messages = messages
        self.registry = registry
        self._cancel_event = threading.Event()
        self.sanitize = sublime.load_settings(
            "Agentic.sublime-settings").get("sanitize_output")
        if self.sanitize:
            _update_sanitize_dict()
        self.show_reasoning = sublime.load_settings(
            "Agentic.sublime-settings").get("show_reasoning")
        self._buffer = []       # pending writes
        self._pending = False   # a flush is already scheduled?
        self.start_time = time.time()

    def cancel(self):
        self._cancel_event.set()
        self.view.settings().set("agentic_is_streaming", False)

    def run(self):
        prev_reasoning = False
        cache = 0.0
        tps = None

        self._write("\n\n# --- Agent ---\n")
        sublime.set_timeout(
            lambda: self.view.run_command(
                "move_to", {"to": "eof", "extend": False}), 0)

        if self.view.settings().get("agent_model") is None:
            self.view.settings().set(
                "agent_model",
                _pick_model())

        # Load latest model settings
        models = sublime.load_settings("Agentic.sublime-settings").get("models")
        model_name = self.view.settings().get("agent_model")
        model = models[model_name]

        # Estimate initial prompt tokens
        input_tokens = int(sum([
            len(m.get("content", "")) for m in self.messages
            if isinstance(m.get("content"), str)
        ]) / CHARS_PER_TOKEN)

        context_limit = model.get("context")
        if context_limit:
            status_streaming = "Streaming {}. Context: {} ({:.0f}%)...".format(
                model_name, input_tokens, (input_tokens / context_limit) * 100)
        else:
            status_streaming = "Streaming {}. Context: {}...".format(
                model_name, input_tokens)

        _printstatus(status_streaming)
        if self.is_valid():
            self.view.set_status("agentic_stats", status_streaming)

        output_chars = 0
        try:
            for chunk in chat_stream(self.messages, model, self._cancel_event):
                # Normal (2-tuple) chunk vs. metrics (4-tuple)
                if len(chunk) == 2:
                    is_reasoning, text = chunk
                    output_chars += len(text)
                else:
                    cache, prompt, pps, tps = chunk
                    if prompt:
                        input_tokens = prompt + cache
                    break

                if self._cancel_event.is_set() or not self.is_valid():
                    self._cancel_event.set()
                    _printstatus("Interrupted {}".format(model_name))
                    if self.is_valid():
                        self.view.set_status("agentic_stats", "{}: Interrupted".format(model_name))
                    break

                if is_reasoning:
                    if not self.show_reasoning:
                        continue
                    if not prev_reasoning:  # first reasoning message
                        self._write("\n## --- Thinking ---\n>")
                        prev_reasoning = True
                    text = text.replace("\n", "\n> ")
                else:
                    if prev_reasoning:
                        self._write("\n\n## --- Response ---\n")
                        prev_reasoning = False
                self._write(text)

        except Exception as e:
            error_details = ""
            try:
                error_details = "\n" + e.read().decode('utf-8')
            except Exception:
                pass
            print(
                "Could not stream from {}:\n"
                "  model: {}\n  url: {}\n  options: {}\nError: {}{}".format(
                    model_name,
                    model.get("model", '"model" field missing'),
                    model.get("url", '"url" field missing'),
                    model.get("options", {}),
                    str(e),
                    error_details))
            sublime.status_message("Streaming Error: {}".format(str(e)))
            if self.is_valid():
                self.view.set_status("agentic_stats", "{}: Error - {}".format(model_name, str(e)))
            sublime.set_timeout(self._finalize, 0)
            return

        # Measure generated tokens and real throughput
        elapsed = max(time.time() - self.start_time, 0.001)
        output_tokens = int(output_chars / CHARS_PER_TOKEN)

        # Fallback to calculated speed if provider metrics are absent or close to zero
        if not tps or tps != tps or tps < 0.1:
            if output_tokens > 0:
                tps = output_tokens / elapsed
            else:
                tps = model.get("speed", 0.0) or 0.0

        # Compute total context usage and fraction
        used_context = input_tokens + output_tokens
        if context_limit:
            fraction_used = (used_context / context_limit) * 100
            context_str = "{} ({:.0f}%)".format(used_context, fraction_used)
        else:
            context_str = "{}".format(used_context)

        # Calculate cost
        cost = 0.0
        uncached_input = max(0, input_tokens - int(cache))

        ## a. Input / output separated costs
        if "input_cost" in model and "output_cost" in model:
            i_token_cost = float(model.get("input_cost", 0.0)) * 1e-6
            o_token_cost = float(model.get("output_cost", 0.0)) * 1e-6
            # Use specific cache_cost or default to 75% discount (0.25x)
            c_token_cost = (float(model.get("cache_cost")) * 1e-6) if "cache_cost" in model else (i_token_cost * 0.25)
            cost = (cache * c_token_cost) \
                 + (uncached_input * i_token_cost) \
                 + (output_tokens * o_token_cost)

        ## b. Average blended cost
        elif "cost" in model:
            token_cost = float(model.get("cost", 0.0)) * 1e-6
            cost = (cache * 0.02 * token_cost) \
                 + (uncached_input * 0.2 * token_cost) \
                 + (output_tokens * token_cost)

        if cost <= 0.0:
            cost_str = "$0.0000"
        elif cost < 0.01:
            cost_str = "${:.4f}".format(cost)
        else:
            cost_str = "${:.3f}".format(cost)

        tps_int = int(tps) if (tps and tps == tps) else 0
        status_done = "{}. Tk/s: {}. Context: {}. Cost: {}".format(
            model_name, tps_int, context_str, cost_str)

        # Log status and persist in status bar
        _printstatus("Streaming Done. " + status_done)
        if self.is_valid():
            self.view.set_status("agentic_stats", status_done)
        sublime.set_timeout(self._finalize, 0)

    def _write(self, txt):
        self._buffer.append(txt)            # atomic under the GIL
        time = 25 if len(self._buffer) < 96 else 0
        if not self._pending:               # schedule a debounced flush
            self._pending = True
            sublime.set_timeout(self.__flush, time)  # balance responsiveness and power

    def is_valid(self):
        return self.view and self.view.is_valid() \
                and self.view.window() and self.view.window().is_valid()

    def __flush(self):
        """Do not run outside of sublime.set_timeout"""
        self._pending = False
        if not self.is_valid():
            self._buffer.clear()
            return

        parts = []
        while self._buffer:                 # pop everything (O(1) each)
            parts.append(self._buffer.pop())
        if not parts:
            return

        out_string = "".join(parts[::-1])
        if self.sanitize:
            out_string = _sanitize_text(out_string)

        self.view.run_command(
            "append",
            {"characters": out_string}  # restore original order
        )

    def _finalize(self):
        if not self.is_valid():
            return
        self.registry.pop(self.view.id(), None)

        # Flush any remaining buffer chunks to the view before reading
        self.__flush()

        # Inspect completed response for SEARCH/REPLACE blocks
        full_chat = self.view.substr(sublime.Region(0, self.view.size()))
        # Grab the last Agent response block
        agent_marker = "# --- Agent ---"
        if agent_marker in full_chat:
            last_agent_text = full_chat.split(agent_marker)[-1]
            blocks = parse_patch_blocks(last_agent_text)
            if blocks:
                patch_results = apply_parsed_patches(self.view.window(), blocks)
                if patch_results:
                    summary = "\n\n**File Edits Applied:**\n" + "\n".join(" - " + r for r in patch_results) + "\n"
                    self.view.run_command("append", {"characters": summary})

        self._write("\n\n# --- User ---\n")
        self.view.settings().set("agentic_is_streaming", False)


def start_streaming(view, messages, model_name=None):
    """Public helper - start a streaming task on a view. Returns True if started, False if aborted."""
    # Pre-flight file validation and in-memory injection
    window = view.window() if view else None
    success, processed_messages, error_details = process_messages_file_mentions(
        messages, window=window, view=view
    )
    if not success:
        view.settings().set("agentic_is_streaming", False)
        view.set_status("agentic_stats", "Aborted: Unresolved file reference")
        sublime.error_message("Agentic - Unresolved File Reference\n\n" + error_details)
        return False

    view.settings().set("agentic_is_streaming", True)
    if model_name:
        view.settings().set("agent_model", model_name)
    task = AgentStreamingTask(view, processed_messages, _ACTIVE_STREAMERS)
    _ACTIVE_STREAMERS[view.id()] = task
    task.start()
    return True


def _build_messages_from_text(text):
    """Parse a view into a list of chat messages"""
    lines = text.splitlines(True)

    messages = []
    current_role = None
    content_lines = []
    in_reasoning = False

    for line in lines:
        stripped = line.strip()

        # Block start
        if stripped.startswith("# --- "):
            if current_role and content_lines:
                msg = "".join(content_lines).rstrip("\n")
                messages.append({"role": current_role, "content": msg})

            if "System" in stripped:
                current_role = "developer"
            elif "User" in stripped:
                current_role = "user"
            elif "Agent" in stripped:
                current_role = "assistant"
            else:
                current_role = None

            content_lines = []
            continue

        # Reasoning section toggles
        if stripped.startswith("## --- "):
            if "Thinking" in stripped:
                in_reasoning = True
            elif "Response" in stripped:
                in_reasoning = False
            continue

        if in_reasoning:
            continue

        if current_role:
            content_lines.append(line)

    # Finalise the last block
    if current_role:
        msg = "".join(content_lines).rstrip("\n")
        messages.append({"role": current_role, "content": msg})

    return messages


def _rebuild_text(messages, strip_active=False):
    """Reconstructs chat text from message list"""
    if not messages:
        return ""
    parts = []
    for m in messages:
        tag = TAG_MAP.get(m["role"])
        if not tag:
            continue
        parts.append("{}\n{}\n".format(tag, m["content"].strip()))
    if strip_active and parts and parts[-1].startswith(TAG_MAP["assistant"]) \
            and len(parts[-1]) < 19:
        parts = parts[:-1]
    return "\n".join(parts)[:-1]


def _create_chat(window, name, initial="",
                 temporary=True, create_pane=True, pane_dir="right"):
    view = window.new_file()
    if create_pane:
        window.set_layout({
            "cols":  [0.0, 0.5, 1.0],
            "rows":  [0.0, 1.0],
            "cells": [[0, 0, 1, 1],  # left group
                      [1, 0, 2, 1]]  # right group
        })
        right_group = window.num_groups() - 1
        window.set_view_index(view, right_group, 0)  # move file
        window.focus_group(right_group)
        # # Origami:
        # window.run_command("create_pane_with_file", {"direction": pane_dir})
    if temporary:
        view.set_scratch(True)
    view.set_name(name)
    view.settings().set("agentic_is_chat", True)
    view.set_syntax_file("Packages/Markdown/Markdown.sublime-syntax")
    if initial:
        view.run_command("append", {"characters": initial})
        sublime.set_timeout(
            lambda: view.run_command("move_to", {"to": "eof", "extend": False}),
            0)
    return view


def _read_selection(view):
    """Load user-selected data or file"""
    sel = view.sel()
    if any(not r.empty() for r in sel):
        content = "\n...\n".join(view.substr(r)
                                 for r in sel if not r.empty())
    else:  # load entire file:
        content = view.substr(sublime.Region(0, view.size()))
    return content

def _resolve_target_path(filepath, window):
    """
    Resolve target filepath for existing or new files.
    Tries resolve_path first. If not found on disk, resolves relative to:
      1. First window project folder
      2. Directory of active view file
      3. Absolute path / working directory
    """
    resolved = resolve_path(filepath, window=window)
    if resolved: return resolved

    # If it's already an absolute path
    if os.path.isabs(filepath):
        return os.path.normpath(filepath)
    # otherwise, ..
    #+1. Relative to first window project folder
    folders = window.folders() if window else []
    if folders:
        return os.path.normpath(os.path.join(folders[0], filepath))
    # 2. Relative to active file's directory
    if window:
        active_view = window.active_view()
        if active_view and active_view.file_name():
            return os.path.normpath(os.path.join(os.path.dirname(active_view.file_name()), filepath))
    # 3. Fallback to current working directory
    return os.path.normpath(os.path.abspath(filepath))


def apply_parsed_patches(window, blocks):
    """
    Takes a list of FilePatchBlock objects and applies them to open views or files on disk.
    Returns a list of status messages.
    """
    if not blocks or not window:
        return []

    # Group blocks by resolved path
    grouped = {}
    for block in blocks:
        resolved = _resolve_target_path(block.filepath, window=window)
        if not resolved:
            return ["Error: Cannot resolve file path '{}'".format(block.filepath)]
        grouped.setdefault(resolved, []).append(block)

    results = []
    for filepath, file_blocks in grouped.items():
        is_new_file = not os.path.exists(filepath)

        # Check if already open in Sublime
        view = window.find_open_file(filepath) if window else None
        if view and view.is_loading():
            time.sleep(0.1)

        if view and view.is_valid():
            original_content = view.substr(sublime.Region(0, view.size()))
            success, new_content, err = apply_patch_blocks(original_content, file_blocks)
            if success:
                view.run_command("agentic_apply_buffer_patch", {"new_content": new_content})
                action_str = "Created" if (is_new_file or not original_content.strip()) else "Updated"
                results.append("✓ {} {} in editor ({} block{})".format(
                    action_str, os.path.basename(filepath), len(file_blocks), "s" if len(file_blocks) > 1 else ""))
            else:
                results.append("✗ Failed to patch {}: {}".format(os.path.basename(filepath), err))
        else:
            # File is on disk; read, modify, and open in Sublime
            try:
                original_content = ""
                if not is_new_file:
                    with open(filepath, "r", encoding="utf-8") as f:
                        original_content = f.read()

                success, new_content, err = apply_patch_blocks(original_content, file_blocks)
                if success:
                    parent_dir = os.path.dirname(filepath)
                    if parent_dir and not os.path.exists(parent_dir):
                        os.makedirs(parent_dir, exists_ok=True)

                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    if window:
                        window.open_file(filepath)

                    action_str = "Created" if is_new_file else "Updated"
                    results.append("✓ {} {} on disk ({} block{})".format(
                        action_str, os.path.basename(filepath), len(file_blocks), "s" if len(file_blocks) > 1 else ""))
                else:
                    results.append("✗ Failed to patch {}: {}".format(os.path.basename(filepath), err))
            except Exception as e:
                results.append("✗ Error reading {}: {}".format(os.path.basename(filepath), str(e)))

    return results

class PromptInputHandler(sublime_plugin.TextInputHandler):
    """Input handler - free-form prompt for AgenticCodeCommand"""
    def placeholder(self):
        return "Enter command string"

    def initial_text(self):
        return ""


class AgenticCodeCommand(sublime_plugin.WindowCommand):
    """Start a new chat based on highlighted text"""
    def input(self, args):
        return PromptInputHandler()

    def run(self, prompt):
        # New window + scratch view
        old = self.window.active_view()
        if not old: return

        content = _read_selection(old)
        user_prompt = "File: {}\n```\n{}\n```\n{}".format(
            old.file_name(), content, prompt)
        new_chat = "# --- System ---\n{}\n\n# --- User ---\n{}\n".format(
            sublime.load_settings("Agentic.sublime-settings").get("default_prompt"),
            user_prompt
        )

        view = _create_chat(self.window, "Chat " + prompt[:12],
                            initial=new_chat)

        messages = _build_messages_from_text(new_chat)

        # Start agent
        if prompt:  # only launch automatically if user provided a command
            started = start_streaming(view, messages)
            if started:
                sublime.status_message("Submitting prompt")


class AgenticChatCommand(sublime_plugin.WindowCommand):
    """Interact with an existing chat file"""
    def run(self):
        view = self.window.active_view()
        if not view:
            return

        if view.settings().get("agentic_is_streaming"):
            self.window.run_command("cancel_stream")
            return

        text = view.substr(sublime.Region(0, view.size()))
        messages = _build_messages_from_text(text)
        if not messages:
            sublime.status_message("Chat data not found")
            self.window.run_command("agent_new_chat")
            return

        # --- Slash Command Interception ---
        cmd, filepath, prompt = _extract_slash_command(messages)
        if cmd == "edit":
            # If the user typed no prompt after the filename, ask via input panel
            self.window.run_command("agentic_edit_file", {
                "file_path": filepath,
                "prompt": prompt or None
            })
            return
        elif cmd == "create":
            self.window.run_command("agentic_create_file", {
                "file_path": filepath,
                "prompt": prompt or None
            })
            return
        # -----------------------------------

        view.settings().set("agentic_is_chat", True)
        view.set_syntax_file("Packages/Markdown/Markdown.sublime-syntax")

        started = start_streaming(view, messages)
        if started:
            sublime.status_message("Submitting prompt")


class AgenticNewChatCommand(sublime_plugin.WindowCommand):
    """Open a new chat window"""
    def run(self):
        if not self.window.active_view():
            return
        new_chat = "# --- System ---\n{}\n\n# --- User ---\n".format(
            sublime.load_settings("Agentic.sublime-settings").get("default_prompt")
        )
        view = _create_chat(self.window, "Chat", new_chat, create_pane=False)


class AgenticCloneChatCommand(sublime_plugin.WindowCommand):
    """Create a new chat from an existing one"""
    def run(self):
        view = self.window.active_view()
        if not view:
            return
        text = view.substr(sublime.Region(0, view.size()))
        messages = _build_messages_from_text(text)
        if not messages:
            sublime.status_message("Chat data not found")
            self.window.run_command("agent_new_chat")
            return
        cleaned = _rebuild_text(messages, strip_active=True)
        view = _create_chat(self.window, "Chat", cleaned, create_pane=False)


class AgenticCancelStreamCommand(sublime_plugin.WindowCommand):
    """Cancel an active stream command"""
    def run(self):
        view = self.window.active_view()
        if not view or not view.settings().get("agentic_is_streaming"):
            return
        task = _ACTIVE_STREAMERS.get(view.id())
        if task:
            task.cancel()
            _printstatus("Streaming cancelled.")
            view.set_status("agentic_stats", "Streaming cancelled.")
        else:
            view.settings().set("agentic_is_streaming", False)


class AgenticClearReasoningCommand(sublime_plugin.TextCommand):
    """Clear reasoning sections from a finished chat"""
    def run(self, edit):
        view = self.view
        if view.id() in _ACTIVE_STREAMERS:
            return
        text = view.substr(sublime.Region(0, view.size()))
        messages = _build_messages_from_text(text)
        if not messages:
            sublime.status_message("Chat data not found")
            return
        cleaned = _rebuild_text(messages)
        view.replace(edit, sublime.Region(0, view.size()), cleaned)
        sublime.status_message("Chat reasoning cleared")


class AgenticActionCommand(sublime_plugin.WindowCommand):
    """Run a user-defined action - see Agentic.sublime-settings"""
    def run(self):
        self.actions = self._load_actions()
        if not self.actions:
            sublime.error_message(
                "Agentic.sublime-settings contains no actions.\n"
                'Add a JSON array under the key `"actions"`.'
            )
            return

        self.window.show_quick_panel(
            list(self.actions.keys()),
            self.run_action  # callback called with the index chosen by the user
        )

    def run_action(self, index):
        """Called when the user picks an item (or cancels)."""
        if index == -1:  # user pressed Escape
            return
        action_name = list(self.actions.keys())[index]
        chosen = self.actions[action_name]
        settings = sublime.load_settings("Agentic.sublime-settings")
        models_list = chosen["models"]
        system_prompt = chosen["system"]
        prompt = chosen.get("prompt", "")

        old = self.window.active_view()
        if not old: return
        sel = any(not r.empty() for r in old.sel())
        if prompt or sel:
            user_prompt = "File: {}\n```\n{}\n```\n\n{}".format(
                old.file_name(),  _read_selection(old), prompt)
        else:
            user_prompt = ""

        new_chat = "# --- System ---\n{}\n\n# --- User ---\n{}".format(
            system_prompt, user_prompt)

        view = _create_chat(self.window, "Chat " + action_name[:12], new_chat)

        if prompt:
            messages = _build_messages_from_text(new_chat)
            model = _pick_model(models_list)
            started = start_streaming(view, messages, model)
            if started:
                sublime.status_message("Submitting prompt")

    def _load_actions(self):
        return sublime.load_settings("Agentic.sublime-settings").get("actions")


class AgenticModelChatCommand(sublime_plugin.WindowCommand):
    """Start a chat with a specific model - see Agentic.sublime-settings"""
    def run(self):
        self.models = self._load_models()
        if not self.models:
            sublime.error_message(
                "Agentic.sublime-settings contains no models.\n"
                'Add a JSON array under the key `"models"`.'
            )
            return

        self.window.show_quick_panel(
            list(self.models.keys()),
            self.model_chat
        )

    def model_chat(self, index):
        """Called when the user picks an item (or cancels)."""
        if index == -1:  # user pressed Escape
            return
        model_name = list(self.models.keys())[index]
        model = self.models[model_name]

        old = self.window.active_view()
        if not old: return
        sel = old.sel()
        if any(not r.empty() for r in sel):  # load active selection
            content = "\n...\n".join(old.substr(r)
                                     for r in sel if not r.empty())
            user_prompt = "File: {}\n```\n{}\n```\n".format(
                old.file_name(), content)
        else:  # empty new chat
            user_prompt = ""

        new_chat = "# --- System ---\n{}\n\n# --- User ---\n{}".format(
            sublime.load_settings("Agentic.sublime-settings").get("default_prompt"),
            user_prompt)

        view = _create_chat(self.window, "Chat " + model_name[:12], new_chat)

        messages = _build_messages_from_text(new_chat)
        view.settings().set("agent_model", model_name)

    def _load_models(self):
        return sublime.load_settings("Agentic.sublime-settings").get("models")


class AgenticSetModelCommand(sublime_plugin.WindowCommand):
    """Pick a model from your settings and store it in the view."""
    def run(self):
        self._models = sublime.load_settings("Agentic.sublime-settings").get("models")
        if not self._models:
            sublime.error_message("Agentic.sublime-settings contains no models.")
            return

        self._keys = list(self._models.keys())
        self.window.show_quick_panel(self._keys, self._on_done)

    def _on_done(self, index):
        if index == -1:
            return

        model_name = self._keys[index]
        view = self.window.active_view()
        if view:
            view.settings().set("agent_model", model_name)
            sublime.status_message("Model set to %s" % model_name)


class AgenticViewCloseHandler(sublime_plugin.EventListener):
    """
    Close stream when tab (view) closes
    """
    def on_close(self, view):
        if view.settings().get("agentic_is_streaming"):
            task = _ACTIVE_STREAMERS.get(view.id())
            if task:
                task.cancel()
            else:
                view.settings().set("agentic_is_streaming", False)


def _update_sanitize_dict():
    global _LAST_SANITIZE_DICT_RAW
    global _SANITIZE_DICT
    global _SANITIZE_RE

    all_sanitize = sublime.load_settings("Agentic.sublime-settings").get("sanitize_dict")
    # {canonical: [look-alikes]}

    # Do nothing if dict is the same.
    if _LAST_SANITIZE_DICT_RAW is not None \
            and all_sanitize == _LAST_SANITIZE_DICT_RAW:
        return

    if not all_sanitize or not isinstance(all_sanitize, dict):
        _SANITIZE_DICT = {}
        _SANITIZE_RE = None
        _LAST_SANITIZE_DICT_RAW = all_sanitize
        return

    _SANITIZE_DICT = {}
    for _canonical, _alts in all_sanitize.items():
        for _ch in _alts:
            _SANITIZE_DICT[_ch] = _canonical

    if _SANITIZE_DICT:
        _SANITIZE_RE = re.compile(
            "|".join(sorted(map(re.escape, _SANITIZE_DICT), key=len, reverse=True))
        )
    else:
        _SANITIZE_RE = None

    _LAST_SANITIZE_DICT_RAW = all_sanitize


def _sanitize_text(text: str) -> str:
    """
    Replace Unicode look-alike in *text* with its ASCII canonical value
    """
    # `sub` receives each match; we look up the canonical character
    # in the flat map we built above.
    return _SANITIZE_RE.sub(lambda m: _SANITIZE_DICT[m.group(0)], text)


class AgenticSanitizeCommand(sublime_plugin.TextCommand):
    """Sanitize the whole document or the selected text."""
    def run(self, edit):
        _update_sanitize_dict()
        view = self.view

        #  1.  If there is at least one non-empty selection - sanitize it.
        sel = view.sel()
        if any(not r.empty() for r in sel):
            # Replace each selected region independently.
            # Iterate from the end so that earlier offsets stay valid.
            for r in reversed(list(sel)):
                if r.empty():
                    continue
                selected_text = view.substr(r)
                sanitized = _sanitize_text(selected_text)
                view.replace(edit, r, sanitized)
            sublime.status_message("Selection sanitized")
            return

        #  2. No selection - sanitize the entire document.
        region = sublime.Region(0, view.size())
        text = view.substr(region)

        if not text:
            sublime.status_message("Document is empty - nothing to sanitize")
            return

        sanitized = _sanitize_text(text)
        view.replace(edit, region, sanitized)
        sublime.status_message("Document sanitized")


class AgenticCopyCommand(sublime_plugin.TextCommand):
    """Sanitizes AI outputs (removes extra unicode based on settings)"""
    def run(self, edit):
        do_clean = sublime.load_settings("Agentic.sublime-settings").get("sanitize_on_copy")

        # Standard copy if sanitize_on_copy is false
        if not do_clean:
            self.view.run_command("copy")
            return

        _update_sanitize_dict()
        view = self.view
        pieces = []
        for region in view.sel():
            if region.empty():
                raw = view.substr(view.line(region))
            else:
                raw = view.substr(region)
            pieces.append(raw)
        out = _sanitize_text("\n".join(pieces))
        sublime.set_clipboard(out)
        end = "" if len(out) == 1 else "s"
        sublime.status_message("Sanitized + Copied {} character{}".format(len(out), end))


class AgenticCutCommand(sublime_plugin.TextCommand):
    """Sanitizes AI outputs and cuts the selection (copy + delete)."""
    def run(self, edit):
        # Use the same setting that controls copy sanitization
        do_clean = sublime.load_settings("Agentic.sublime-settings").get("sanitize_on_copy")

        # Standard cut if sanitization is disabled
        if not do_clean:
            self.view.run_command("cut")
            return

        # Sanitize the selected text and put it on the clipboard
        _update_sanitize_dict()
        view = self.view
        pieces = []
        for region in view.sel():
            if region.empty():
                raw = view.substr(view.line(region))
            else:
                raw = view.substr(region)
            pieces.append(raw)
        out = _sanitize_text("\n".join(pieces))
        sublime.set_clipboard(out)

        # Delete the original selection(s)
        delete_regions = []
        for region in view.sel():
            delete_regions.append(view.line(region) if region.empty() else region)

        # Delete from the end to the start so offsets don't shift
        for region in sorted(delete_regions, key=lambda r: r.begin(), reverse=True):
            view.erase(edit, region)

        # Status feedback
        end = "" if len(out) == 1 else "s"
        sublime.status_message("Sanitized + Cut {} character{}".format(len(out), end))


class AgenticInsertFileCommand(sublime_plugin.WindowCommand):
    """
    Insert an '@file' reference (chosen from the current project) at the
    current caret position for in-memory injection.
    """
    def run(self):
        self.view = self.window.active_view()
        if not self.view or self.view.settings().get("agentic_is_streaming"):
            return

        # Gather visible files from all project folders
        self._paths, self._names = [], []
        for folder in self.window.folders():
            for root, _, files in os.walk(folder):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, folder)
                    if rel.startswith('.git'):
                        continue
                    self._paths.append(full)
                    self._names.append(rel)

        if not self._paths:
            return

        self._names, self._paths = zip(
            *sorted(zip(self._names, self._paths), key=lambda x: x[0])
        )

        # Let the user pick a file
        self.window.show_quick_panel(list(self._names), self._on_done)

    def _on_done(self, idx):
        if idx < 0:
            return  # user cancelled

        rel_path = self._names[idx]

        # Wrap in quotes if the path contains spaces so the regex matches properly
        if " " in rel_path:
            mention = '@"{}" '.format(rel_path)
        else:
            mention = "@{} ".format(rel_path)

        # Insert reference at each caret position
        self.view.run_command('insert', {'characters': mention})

class AgenticApplyBufferPatchCommand(sublime_plugin.TextCommand):
    """
    Replaces entire buffer content with patched content in a single atomic undo step.
    """
    def run(self, edit, new_content=""):
        region = sublime.Region(0, self.view.size())
        self.view.replace(edit, region, new_content)

EDIT_SYSTEM_INSTRUCTIONS = """
When editing files, you must output one or more SEARCH/REPLACE blocks formatted as follows:

path/to/file.ext
<<<<<<< SEARCH
exact lines to be removed
=======
replacement lines
>>>>>>> REPLACE

Rules:
1. Output the relative path immediately before each SEARCH block.
2. Include enough surrounding lines in SEARCH so the match is completely unique.
3. Keep indentation and whitespace exact.
4. To CREATE a new file, output an empty SEARCH block:
path/to/new/file.ext
<<<<<<< SEARCH
=======
entire content of the new file
>>>>>>> REPLACE
"""

class AgenticEditFileCommand(sublime_plugin.WindowCommand):
    """
    Prompt-driven file editing command.
    Accepts:
      - file_path: target file (defaults to current view file)
      - prompt: editing request
    """
    def run(self, file_path=None, prompt=None):
        view = self.window.active_view()
        if not file_path and view and view.file_name():
            file_path = view.file_name()

        # If path is relative, resolve it relative to window folders
        if file_path:
            file_path = _resolve_target_path(file_path, self.window)

        if not file_path or not os.path.exists(file_path):
            sublime.status_message("File to edit does not exist: {}".format(file_path))
            return

        if not prompt:
            filename = os.path.basename(file_path)
            self.window.show_input_panel(
                "Edit {} - Instruction:".format(filename),
                "",
                lambda user_prompt: self._on_prompt_entered(user_prompt, file_path),
                None,
                None
            )
            return

        self._on_prompt_entered(prompt, file_path)

    def _on_prompt_entered(self, prompt, file_path):
        if not prompt:
            return

        # Prepare user prompt with @file mention
        rel = file_path
        for folder in self.window.folders():
            if file_path.startswith(folder):
                rel = os.path.relpath(file_path, folder)
                break
        user_msg = "Please edit @{}\n\nInstruction: {}".format(rel, prompt)

        # Create system prompt containing the SEARCH/REPLACE rules
        default_system = sublime.load_settings("Agentic.sublime-settings").get("default_prompt", "")
        system_content = default_system + "\n" + EDIT_SYSTEM_INSTRUCTIONS.strip()

        chat_text = "# --- System ---\n{}\n\n# --- User ---\n{}\n".format(system_content, user_msg)
        chat_view = _create_chat(self.window, "Edit " + os.path.basename(file_path), chat_text)

        messages = _build_messages_from_text(chat_text)
        _printstatus("Starting edit for {}".format(os.path.basename(file_path)))
        start_streaming(chat_view, messages)


class AgenticCreateFileCommand(sublime_plugin.WindowCommand):
    """
    Prompt-driven new file creation command.
    Accepts:
      - file_path: target relative or absolute path for the new file
      - prompt: creation instructions/requirements
    """
    def run(self, file_path=None, prompt=None):
        if not file_path:
            self.window.show_input_panel(
                "New file path:",
                "",
                lambda target_path: self._on_filepath_entered(target_path, prompt),
                None,
                None
            )
            return

        if not prompt:
            self._ask_prompt(file_path)
            return

        self._on_prompt_entered(prompt, file_path)

    def _on_filepath_entered(self, file_path, prompt=None):
        file_path = file_path.strip()
        if not file_path:
            return

        if not prompt:
            self._ask_prompt(file_path)
            return

        self._on_prompt_entered(prompt, file_path)

    def _ask_prompt(self, file_path):
        filename = os.path.basename(file_path) or file_path
        self.window.show_input_panel(
            "Create {} - Instruction:".format(filename),
            "",
            lambda p: self._on_prompt_entered(p, file_path),
            None,
            None
        )

    def _on_prompt_entered(self, prompt, file_path):
        if not prompt or not file_path:
            return

        user_msg = "Please create file `{}`\n\nInstruction: {}".format(file_path, prompt)

        # Create system prompt containing the SEARCH/REPLACE rules
        default_system = sublime.load_settings("Agentic.sublime-settings").get("default_prompt", "")
        system_content = default_system + "\n" + EDIT_SYSTEM_INSTRUCTIONS.strip()

        chat_text = "# --- System ---\n{}\n\n# --- User ---\n{}\n".format(system_content, user_msg)
        chat_view = _create_chat(self.window, "Create " + os.path.basename(file_path), chat_text)

        messages = _build_messages_from_text(chat_text)
        _printstatus("Starting file creation for {}".format(os.path.basename(file_path)))
        start_streaming(chat_view, messages)
