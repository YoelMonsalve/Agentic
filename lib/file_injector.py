# Packages/Agentic/lib/file_injector.py
"""
Agentic File Injector Library

Provides automatic in-memory file resolution and context injection for the
'@path/to/file' syntax in Agentic chat prompts.
"""

import os
import re

# Maximum allowed file size for in-memory injection (1 MB)
MAX_FILE_SIZE = 1024 * 1024

# Common extensionless files typically found in software repositories
KNOWN_EXTENSIONLESS_FILES = {
    "makefile", "dockerfile", "license", "readme", "gemfile",
    "rakefile", "procfile", "vagrantfile", "containerfile"
}

# Regex to match @file mentions.
# Uses negative lookbehind to avoid matching:
#   - Email addresses (user@host.com)
#   - Backslash escaped mentions (\@file)
#   - Double-at escaped mentions (@@file)
FILE_MENTION_RE = re.compile(
    r'(?<![\w\.\-\\@])@(?:"([^"\n\r]+)"|\'([^\'\n\r]+)\'|([^\s\n\r\(\)\[\]\{\}\<\>`]+))'
)
# Trailing punctuation to strip from unquoted paths in natural language
TRAILING_PUNCTUATION = ".,:;?!)]}>\'\""

def _strip_markdown_code(text):
    """Remove fenced code blocks and inline code spans."""
    # Strip multiline code blocks (```...```)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Strip inline code (`...`)
    text = re.sub(r'`[^`\r\n]*`', '', text)
    return text

def extract_file_mentions(text):
    """
    Extracts all candidate file paths from '@path' mentions in text.
    Returns a list of raw path strings in order of occurrence.
    """
    if not text or "@" not in text:
        return []

    # Strip markdown code blocks and inline backticks so examples are ignored
    clean_text = _strip_markdown_code(text)

    candidates = []
    for match in FILE_MENTION_RE.finditer(clean_text):
        quoted_double = match.group(1)
        quoted_single = match.group(2)
        unquoted = match.group(3)

        if quoted_double is not None:
            raw_path = quoted_double.strip()
        elif quoted_single is not None:
            raw_path = quoted_single.strip()
        elif unquoted is not None:
            raw_path = unquoted.rstrip(TRAILING_PUNCTUATION)
            # Filter out Python decorators or bare identifiers that are not files
            # (e.g. @property, @classmethod, @override)
            if not _is_plausible_file_path(raw_path):
                continue
        else:
            continue

        if raw_path and raw_path not in candidates:
            candidates.append(raw_path)

    return candidates


def _is_plausible_file_path(token):
    """
    Heuristic to determine if an unquoted token looks like a file path
    rather than a programming language decorator (e.g., @property).
    """
    if not token:
        return False

    # Contains path separators or starts with ~
    if "/" in token or "\\" in token or token.startswith("~"):
        return True

    # Has a file extension (e.g. main.py, config.json)
    base, ext = os.path.splitext(token)
    if ext and len(ext) > 1 and len(ext) <= 8 and ext[1:].isalnum():
        return True

    # Known extensionless filenames
    if token.lower() in KNOWN_EXTENSIONLESS_FILES:
        return True

    return False


def is_binary_file(filepath):
    """
    Checks if a file is binary by inspecting the first 1024 bytes for null bytes.
    """
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except Exception:
        return False


def resolve_path(raw_path, window=None, active_view=None):
    """
    Resolves a raw path candidate using the 5-step resolution hierarchy:
      1. Absolute path: starts with '/'
      2. User-expanded path (2.1): starts with '~'
      3. Project root folders (2.2): folders open in Sublime Text window
      4. Active view parent directory
      5. Current working directory (2.3): os.getcwd()

    Returns the absolute path if it exists as a regular file, else None.
    """
    if not raw_path:
        return None

    # 1. Absolute path
    if raw_path.startswith("/"):
        candidate = os.path.abspath(raw_path)
        return candidate if os.path.isfile(candidate) else None

    # 2. User home-expanded path (~/...)
    if raw_path.startswith("~"):
        candidate = os.path.abspath(os.path.expanduser(raw_path))
        return candidate if os.path.isfile(candidate) else None

    # 3. Project root folders (Sublime Text window.folders())
    if window:
        for folder in window.folders():
            candidate = os.path.abspath(os.path.join(folder, raw_path))
            if os.path.isfile(candidate):
                return candidate

    # 4. Active view parent directory
    if active_view and active_view.file_name():
        parent_dir = os.path.dirname(active_view.file_name())
        candidate = os.path.abspath(os.path.join(parent_dir, raw_path))
        if os.path.isfile(candidate):
            return candidate

    if window:
        for v in window.views():
            fname = v.file_name()
            if fname:
                parent_dir = os.path.dirname(fname)
                candidate = os.path.abspath(os.path.join(parent_dir, raw_path))
                if os.path.isfile(candidate):
                    return candidate

    # 5. Process working directory (Sublime cwd)
    candidate = os.path.abspath(os.path.join(os.getcwd(), raw_path))
    if os.path.isfile(candidate):
        return candidate

    return None


def read_file_safe(resolved_path):
    """
    Safely reads text content from a resolved file path.
    Returns (success: bool, content_or_error: str).
    """
    try:
        file_size = os.path.getsize(resolved_path)
        if file_size > MAX_FILE_SIZE:
            return False, "File exceeds maximum size limit of 1 MB (size: {:.1f} KB)".format(file_size / 1024)

        if is_binary_file(resolved_path):
            return False, "Binary file cannot be injected into chat context"

        with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return True, content
    except Exception as e:
        return False, "Could not read file: {}".format(str(e))


def format_file_attachment(raw_path, resolved_path, content):
    """
    Formats the file content as a markdown block for in-memory context attachment.
    """
    ext = os.path.splitext(resolved_path)[1].lstrip(".")
    lang = ext if ext else ""

    # Choose a markdown fence that does not collide with file contents
    fence = "```"
    while fence in content:
        fence += "`"

    header = "\n\n---\n### Attached Context: `{}`\nPath: `{}`\n{}{}\n{}\n{}".format(
        raw_path, resolved_path, fence, lang, content, fence
    )
    return header

def _unescape_mentions(text):
    """Replaces \@ and @@ with @ for final model consumption."""
    return re.sub(r'(\\+@|@@)', '@', text)

def process_messages_file_mentions(messages, window=None, view=None):
    """
    Pre-flight validation and in-memory injection.
    Inspects all user messages for '@file' mentions.

    If any referenced file cannot be resolved or read:
      Returns (False, None, error_details: str)

    If all references resolve successfully:
      Returns (True, updated_messages: list, None)
    """
    updated_messages = []
    unresolved_refs = []
    read_errors = []

    # Track already injected files across the conversation to avoid redundant injection
    globally_attached_paths = set()

    for msg in messages:
        # We only look for mentions in user prompts
        if msg.get("role") != "user":
            updated_messages.append(dict(msg))
            continue

        content = msg.get("content", "")
        mentions = extract_file_mentions(content)

        if not mentions:
            new_msg = dict(msg)
            new_msg["content"] = _unescape_mentions(content)
            updated_messages.append(new_msg)
            continue

        attachments = []

        for raw_path in mentions:
            resolved = resolve_path(raw_path, window=window, active_view=view)

            if not resolved:
                unresolved_refs.append("@" + raw_path)
                continue

            if resolved in globally_attached_paths:
                # Already attached in an earlier mention; don't duplicate tokens
                continue

            success, file_content_or_err = read_file_safe(resolved)
            if not success:
                read_errors.append("@{} ({})".format(raw_path, file_content_or_err))
                continue

            globally_attached_paths.add(resolved)
            attachment = format_file_attachment(raw_path, resolved, file_content_or_err)
            attachments.append(attachment)

        if not unresolved_refs and not read_errors:
            # Attach the resolved files in-memory to this user message
            new_content = _unescape_mentions(content) + "".join(attachments)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            updated_messages.append(new_msg)

    # If any error occurred, halt and compile details for the user
    if unresolved_refs or read_errors:
        error_lines = []
        if unresolved_refs:
            error_lines.append("Could not find the following referenced file(s):")
            for ref in unresolved_refs:
                error_lines.append("  • " + ref)
        if read_errors:
            if error_lines:
                error_lines.append("")
            error_lines.append("Unreadable file reference(s):")
            for err in read_errors:
                error_lines.append("  • " + err)

        error_lines.append("\nPlease check the file path(s) or remove the reference(s) before submitting.")
        return False, None, "\n".join(error_lines)

    return True, updated_messages, None
