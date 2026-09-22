# Packages/Agentic/lib/inspector.py
import os
import json
import re
import urllib.request
import fnmatch

DEFAULT_IGNORED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".venv", "venv", "env", ".env", "dist", "build",
    "target", ".idea", ".vscode", ".next", ".nuxt", "coverage", ".tox"
}

BINARY_OR_UNWANTED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib",
    ".bin", ".pyc", ".pyo", ".class", ".jar", ".war", ".lock", ".min.js",
    ".min.css", ".map", ".sqlite", ".db", ".sqlite3"
}


def is_ignored(path, root_folder, custom_patterns=None):
    """Check whether a path matches ignored directories or file patterns."""
    rel = os.path.relpath(path, root_folder)
    parts = rel.split(os.sep)

    for part in parts:
        if part in DEFAULT_IGNORED_DIRS or (part.startswith(".") and part != "."):
            return True

    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_OR_UNWANTED_EXTENSIONS:
        return True

    if custom_patterns:
        filename = os.path.basename(path)
        for pat in custom_patterns:
            if fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(rel, pat):
                return True

    return False


def scan_folder(folder_path, max_files=400, custom_patterns=None):
    """
    Recursively scans folder_path, returning a list of relative file paths.
    """
    folder_path = os.path.abspath(folder_path)
    if not os.path.isdir(folder_path):
        return []

    collected = []
    for root, dirs, files in os.walk(folder_path):
        # Prune ignored directories in-place to avoid unnecessary traversal
        dirs[:] = [
            d for d in dirs
            if not is_ignored(os.path.join(root, d), folder_path, custom_patterns)
        ]

        for f in files:
            full_path = os.path.join(root, f)
            if not is_ignored(full_path, folder_path, custom_patterns):
                rel = os.path.relpath(full_path, folder_path)
                collected.append(rel)
                if len(collected) >= max_files:
                    return collected

    return collected


def query_llm_for_candidates(file_list, prompt, model_info, max_candidates=6):
    """
    Asks the model to pick up to max_candidates files from file_list
    relevant to prompt. Returns a list of relative paths.
    """
    url = model_info.get("url")
    token = model_info.get("token")
    model_name = model_info.get("model")
    options = dict(model_info.get("options", {}))

    system_instruction = (
        "You are an expert code navigation and discovery agent. "
        "Given a list of project files and a user problem or inquiry, identify which files "
        "are most relevant to understanding or resolving the issue.\n"
        "Rules:\n"
        "1. Select at most {} files.\n"
        "2. Respond ONLY with a valid JSON array of relative file paths, e.g. [\"foo/bar.py\", \"baz/qux.ts\"].\n"
        "3. Do not include markdown explanations, markdown code blocks, or extra text."
    ).format(max_candidates)

    user_content = (
        "User Inquiry:\n{}\n\n"
        "Available Files in Folder (total {}):\n{}"
    ).format(
        prompt,
        len(file_list),
        "\n".join(file_list)
    )

    messages = [
        {"role": "developer" if not model_info.get("no_system_prompt") else "user", "content": system_instruction},
        {"role": "user", "content": user_content}
    ]

    body = dict(options)
    body.update({
        "messages": messages,
        "model": model_name,
        "stream": False,
        "temperature": 0.1
    })

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(token),
            "User-Agent": "AsyncAPIClient/Python 2.14.0"
        }
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw_resp = json.loads(resp.read().decode("utf-8"))
        content = raw_resp["choices"][0]["message"].get("content", "").strip()

    # Extract JSON array from model output
    match = re.search(r"\[[\s\S]*?\]", content)
    if match:
        try:
            candidates = json.loads(match.group(0))
            if isinstance(candidates, list):
                # Normalize and ensure candidate exists in file_list
                file_set = set(os.path.normpath(f) for f in file_list)
                valid = []
                for c in candidates:
                    norm = os.path.normpath(str(c))
                    if norm in file_set and norm not in valid:
                        valid.append(norm)
                return valid[:max_candidates]
        except Exception:
            pass

    return []
