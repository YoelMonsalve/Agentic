# Packages/Agentic/lib/patch_engine.py
"""
Agentic Patch Engine Library

Locates SEARCH blocks inside target file content using a multi-tiered matching pipeline:
  1. Exact Substring Match
  2. Whitespace / Line-Ending Normalized Match
  3. Sliding-Window Similarity Heuristic (difflib)

Provides transactional patch application: if any block for a file fails,
the entire file remains untouched.
"""

import difflib


class PatchError(Exception):
    """Raised when a patch block cannot be applied."""
    pass


def _normalize_line(line):
    """Strip trailing whitespace and convert line endings."""
    return line.rstrip(" \t\r\n")


def _find_exact(content, search_text):
    """Tier 1: Exact substring match."""
    count = content.count(search_text)
    if count == 1:
        idx = content.find(search_text)
        return idx, idx + len(search_text)
    if count > 1:
        raise PatchError("SEARCH block matches multiple locations in the file. Provide more surrounding context.")
    return None


def _find_normalized_lines(content_lines, search_lines):
    """Tier 2: Normalized lines match (ignores trailing spaces and line-ending variances)."""
    norm_content = [_normalize_line(l) for l in content_lines]
    norm_search = [_normalize_line(l) for l in search_lines]

    search_len = len(norm_search)
    if search_len == 0 or search_len > len(norm_content):
        return None

    matches = []
    for i in range(len(norm_content) - search_len + 1):
        if norm_content[i:i + search_len] == norm_search:
            matches.append(i)

    if len(matches) == 1:
        start_line = matches[0]
        return start_line, start_line + search_len
    if len(matches) > 1:
        raise PatchError("SEARCH block matches multiple locations under normalized whitespace.")
    return None


def _find_fuzzy_window(content_lines, search_lines, min_similarity=0.80):
    """
    Tier 3: Sliding window similarity match using difflib.SequenceMatcher.
    Searches for windows of lines similar to the SEARCH block.
    """
    search_len = len(search_lines)
    if search_len == 0 or search_len > len(content_lines):
        return None

    search_str = "\n".join(_normalize_line(l) for l in search_lines)

    best_ratio = 0.0
    best_start = -1
    best_end = -1
    
    # Store non-overlapping candidate matches: (ratio, start_line, end_line)
    candidates = []

    # Slide across content lines
    for win_len in range(max(1, search_len - 1), min(len(content_lines) + 1, search_len + 2)):
        for i in range(len(content_lines) - win_len + 1):
            window_str = "\n".join(_normalize_line(l) for l in content_lines[i:i + win_len])
            matcher = difflib.SequenceMatcher(None, search_str, window_str)
            ratio = matcher.ratio()

            if ratio >= min_similarity:
                candidates.append((ratio, i, i + win_len))

    if not candidates:
        return None

    # Sort candidates by similarity ratio descending
    candidates.sort(key=lambda c: c[0], reverse=True)
    best_ratio, best_start, best_end = candidates[0]

    # Check for genuine ambiguity: find any rival match that does NOT overlap with the best match
    for rival_ratio, rival_start, rival_end in candidates[1:]:
        # If rival overlaps with best match, it's just a boundary variance of the same region
        overlaps = max(best_start, rival_start) < min(best_end, rival_end)
        if not overlaps:
            if (best_ratio - rival_ratio) < 0.10:
                raise PatchError("SEARCH block matches multiple distinct locations with similar confidence.")

    return best_start, best_end


def apply_single_block(current_content, search_text, replace_text):
    """
    Applies a single SEARCH/REPLACE block to current_content.
    Returns the new updated string, or raises PatchError.
    """
    # New file / empty file creation
    if not search_text.strip():
        if not current_content.strip():
            return replace_text
        raise PatchError("SEARCH block is empty, but the target file is not empty. Provide context to match.")
             
    # 1. Exact match attempt
    match_span = _find_exact(current_content, search_text)
    if match_span:
        start, end = match_span
        return current_content[:start] + replace_text + current_content[end:]

    content_lines = current_content.splitlines(True)
    search_lines = search_text.splitlines(True)

    # 2. Normalized lines match attempt
    line_span = _find_normalized_lines(content_lines, search_lines)
    if line_span:
        start_line, end_line = line_span
        new_lines = content_lines[:start_line] + [replace_text] + content_lines[end_line:]
        return "".join(new_lines)

    # 3. Fuzzy sliding window match attempt
    fuzzy_span = _find_fuzzy_window(content_lines, search_lines)
    if fuzzy_span:
        start_line, end_line = fuzzy_span
        new_lines = content_lines[:start_line] + [replace_text] + content_lines[end_line:]
        return "".join(new_lines)

    first_line = search_lines[0].strip() if search_lines else ""
    raise PatchError("Could not locate SEARCH block in target file. First line: '{}'".format(first_line))


def apply_patch_blocks(original_content, blocks):
    """
    Applies a list of FilePatchBlock objects transactionally to original_content.
    If all succeed, returns (True, new_content, None).
    If any block fails, returns (False, original_content, error_message).
    """
    content = original_content
    for idx, block in enumerate(blocks):
        try:
            content = apply_single_block(content, block.search_text, block.replace_text)
        except PatchError as e:
            return False, original_content, "Block #{} failed: {}".format(idx + 1, str(e))
        except Exception as e:
            return False, original_content, "Block #{} unexpected error: {}".format(idx + 1, str(e))

    return True, content, None
