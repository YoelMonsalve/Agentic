# Packages/Agentic/lib/patch_parser.py
"""
Agentic Patch Parser Library

This module extracts `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` blocks and associates each block with its target filepath from LLM Markdown output.

Parses Aider-style SEARCH/REPLACE blocks from LLM markdown responses
and pairs them with target filepaths.
"""

import os
import re

# Block delimiter regex patterns
_SEARCH_START_RE = re.compile(r'^[ \t]*<{5,9}[ \t]*SEARCH[ \t]*$', re.MULTILINE)
_DIVIDER_RE = re.compile(r'^[ \t]*={5,9}[ \t]*$', re.MULTILINE)
_REPLACE_END_RE = re.compile(r'^[ \t]*>{5,9}[ \t]*REPLACE[ \t]*$', re.MULTILINE)

# Candidate file path cleaner
_FENCE_OR_HEADER_STRIP = re.compile(r'^[`#*\s]+|[`#*\s:]+$')


class FilePatchBlock:
    """Represents a single SEARCH/REPLACE block for a specific file."""
    def __init__(self, filepath, search_text, replace_text):
        self.filepath = filepath.strip()
        self.search_text = search_text
        self.replace_text = replace_text

    def __repr__(self):
        return "<FilePatchBlock path='{}' search_len={} replace_len={}>".format(
            self.filepath, len(self.search_text), len(self.replace_text)
        )


def _extract_filepath_candidate(preceding_text):
    """
    Scans backward through preceding text to find the most plausible file path.
    Handles headers (### src/foo.py), bold text (**src/foo.py**), inline code (`src/foo.py`),
    or bare text lines immediately above the SEARCH block.
    """
    lines = [line.strip() for line in preceding_text.splitlines() if line.strip()]
    if not lines:
        return None

    # Check the last 5 non-empty lines in reverse
    for line in reversed(lines[-5:]):
        # Strip markdown syntax: `#`, `*`, `` ` ``, `:`
        cleaned = _FENCE_OR_HEADER_STRIP.sub('', line).strip()
        # Strip quotes if wrapped
        if (cleaned.startswith('"') and cleaned.endswith('"')) or \
           (cleaned.startswith("'") and cleaned.endswith("'")):
            cleaned = cleaned[1:-1].strip()

        # Check if line contains common path characteristics
        if "/" in cleaned or "\\" in cleaned or "." in os.path.basename(cleaned):
            # Avoid picking full natural language sentences
            words = cleaned.split()
            if len(words) == 1:
                return cleaned
            # If the line ends with a path-like token
            last_word = _FENCE_OR_HEADER_STRIP.sub('', words[-1]).strip()
            if "/" in last_word or "\\" in last_word or "." in os.path.basename(last_word):
                return last_word

    return None


def parse_patch_blocks(response_text):
    """
    Parses all SEARCH/REPLACE blocks in response_text.
    Returns a list of FilePatchBlock instances.
    """
    blocks = []
    if not response_text or "<<<<<<< SEARCH" not in response_text:
        return blocks

    # Split text by SEARCH markers
    # Each segment after a SEARCH marker starts with the search content
    search_matches = list(_SEARCH_START_RE.finditer(response_text))
    if not search_matches:
        return blocks

    last_known_filepath = None

    for match in search_matches:
        search_start = match.end()
        preceding_text = response_text[:match.start()]

        candidate_path = _extract_filepath_candidate(preceding_text)
        if candidate_path:
            last_known_filepath = candidate_path

        # Look for the dividing ======= marker
        div_match = _DIVIDER_RE.search(response_text, search_start)
        if not div_match:
            continue
        search_end = div_match.start()
        replace_start = div_match.end()

        # Look for the closing >>>>>>> REPLACE marker
        end_match = _REPLACE_END_RE.search(response_text, replace_start)
        if not end_match:
            continue
        replace_end = end_match.start()

        # Extract search and replace text
        # If the search text starts with a newline, strip only the initial newline
        raw_search = response_text[search_start:search_end]
        if raw_search.startswith("\r\n"):
            search_text = raw_search[2:]
        elif raw_search.startswith("\n"):
            search_text = raw_search[1:]
        else:
            search_text = raw_search

        raw_replace = response_text[replace_start:replace_end]
        if raw_replace.startswith("\r\n"):
            replace_text = raw_replace[2:]
        elif raw_replace.startswith("\n"):
            replace_text = raw_replace[1:]
        else:
            replace_text = raw_replace

        if last_known_filepath:
            blocks.append(FilePatchBlock(last_known_filepath, search_text, replace_text))

    return blocks