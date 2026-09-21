# --- System ---
You are an expert programming agent. Focus on correctness and simplicity.

# --- User ---
Read @docs/FILE_INJECTION.md to acquire context on what this session is about.
Basically, I am trying to custom my own version of the `Agentic` package on Sublime Text, making it close to the behavior of CLI agentic tools, like OpenCode, Aider, Claude Code, etc. I'd like to end up having a minimal version of these IDEs.

# --- Agent ---
I have reviewed `docs/FILE_INJECTION.md`. The `@path` in-memory attachment system provides a solid foundation for context gathering (project-relative resolution, pre-flight safety checks, binary detection, and multi-turn persistence) matching how tools like Claude Code and Aider ingest files into prompt context.

---

### What Makes CLI Agents (Aider, Claude Code, OpenCode) Tick

To transition `Agentic` from a chat assistant with context injection into a **true agentic IDE tool**, there are four core pillars:

```
┌─────────────────────────────────────────────────────────────┐
│                       Agentic Loop                          │
├──────────────┬───────────────────┬──────────────────────────┤
│ 1. Context   │ 2. Tool Execution │ 3. Code Modifications    │
│  - @file     │  - grep_search    │  - SEARCH/REPLACE blocks │
│  - Repo Map  │  - file_list      │  - Direct patch/write    │
│  - Active tab│  - terminal/cmd   │  - Sublime undo stack    │
├──────────────┴───────────────────┴──────────────────────────┤
│ 4. Interactive UX (Slash commands, diff review, confirmations)
└─────────────────────────────────────────────────────────────┘
```

---

### Key Architectural Pillars to Implement

#### 1. The Autonomous Agent Loop (Tool Calling / Function Calling)
Unlike standard chat where `User -> Model -> Response -> Done`, an agent operates in a loop:
1. User gives a prompt (e.g. *"Refactor auth middleware to use async JWT validation and update the unit test"*).
2. Model emits tool calls (e.g. `view_file`, `grep_search`, `edit_file`, or `run_command`).
3. Agentic executes the tool locally in Sublime Text, gathers outputs (or errors), feeds them back to the LLM.
4. LLM repeats until it declares task completion.

#### 2. Robust File Editing & Patching
CLI tools handle file editing in two main ways:
- **Search & Replace Blocks (Aider-style):**
  ````markdown
  <<<<<<< SEARCH
  def verify_token(token):
  =======
  async def verify_token(token):
  >>>>>>> REPLACE
  ````
- **Structured Tool Calls (Claude Code / OpenCode style):**
  JSON tool calling passing `{ "path": "...", "old_str": "...", "new_str": "..." }` or full file write.
- **Sublime Native Integration:** Because Agentic runs inside Sublime Text, we can:
  - Open target files into tabs or background buffers.
  - Apply edits through Sublime's TextBuffer API (`Edit` object), preserving the native **Undo/Redo** stack (`Ctrl+Z`).
  - Optionally display unified diffs or open a side-by-side diff tab before committing changes.

#### 3. Workspace Discovery & Symbols (Repo Map / Grep)
CLI agents allow the model to explore without requiring manual `@` references for everything:
- Quick symbol list / ctags / project tree summary.
- Regex search tool (`rg` or Python fallback) to let the model locate relevant files autonomously.

#### 4. Slash Commands & Workflow Controls
CLI tools rely on concise commands inside the chat buffer or command palette:
- `/add <file>` or `@file` (already started)
- `/drop <file>`
- `/undo` (revert last agent changes via git or Sublime undo)
- `/diff` (review unstaged changes)
- `/run <command>` (execute tests/linter and feed back output)

---

### Next Steps

To structure this cleanly, what would you like to tackle first? Some natural starting points:

1. **File Editing Engine:** Implement Search/Replace block parser or structured edit tool that directly modifies files in Sublime Text with undo support.
2. **Tool-Calling Agent Loop:** Wire the LLM loop (OpenAI / Anthropic / Ollama tool schemas) so the model can inspect, edit, and verify iteratively.
3. **Workspace Exploration Tools:** Add `list_directory` and `search_project` (grep) tools for the agent.
4. **Slash Commands & UI Controls:** Add commands for `/diff`, `/undo`, `/clear`, etc.

# --- User ---
I was precisely thinking about point 1: 1. **File Editing Engine:** Implement Search/Replace block parser or structured edit tool that directly modifies files in Sublime Text with undo support.

This initial conversation is not still about coding, instead I'd like first define and refine a strategy.

You mentioned two possible approaches:

- **Search & Replace Blocks (Aider-style):**
  ````markdown
  <<<<<<< SEARCH
  def verify_token(token):
  =======
  async def verify_token(token):
  >>>>>>> REPLACE
  ````
and

- **Structured Tool Calls (Claude Code / OpenCode style):**
  JSON tool calling passing `{ "path": "...", "old_str": "...", "new_str": "..." }` or full file write.

Tell me a little about how **Aider** handles file editing.


# --- Agent ---
Aider’s file editing strategy is widely considered one of the most effective and resilient implementations in the AI CLI ecosystem. 

Here is how Aider handles file editing behind the scenes, why it was designed that way, and the mechanics that make it work.

---

### 1. The Core Problem Aider Solved: Why Not Unified Diffs?

Before SEARCH/REPLACE, early coding tools tried using standard git unified diffs (`patch` format):
```diff
--- a/auth.py
+++ b/auth.py
@@ -45,6 +45,7 @@
 def login():
+    verify_token()
     return True
```

**Why it failed:** LLMs are notoriously terrible at counting line numbers and line offsets. They constantly hallucinate the chunk headers (`@@ -45,6 +45,7 @@`), leading to broken patches that `patch` or `git apply` reject.

Aider invented (and popularized) **SEARCH/REPLACE blocks** because they eliminate line numbers entirely. The model only needs to quote what already exists and what to swap it with.

---

### 2. The Grammar & Format

Aider instructs the model to output edits directly in Markdown responses using this structure:

````markdown
path/to/file.py
<<<<<<< SEARCH
def old_function():
    return 1
=======
def new_function():
    return 2
>>>>>>> REPLACE
````

Key characteristics of this syntax:
- **No line numbers:** The location is determined by matching the text inside `<<<<<<< SEARCH`.
- **Target path declaration:** Stated immediately before the opening block.
- **Multiple blocks per file:** The model can emit multiple SEARCH/REPLACE pairs in a single response to touch disparate sections of the same file.
- **Natural conversational output:** The model can explain *why* it is making a change in plain Markdown before or after the block.

---

### 3. The Matching Engine (The "Secret Sauce")

Extracting the text between `<<<<<<< SEARCH` and `=======` is trivial. The hard part is **locating that exact block inside the actual file on disk**.

LLMs frequently make minor mistakes when reproducing existing code:
- Truncating or modifying indentation (tabs vs. spaces).
- Hallucinating blank lines.
- Skipping comments or changing single quotes to double quotes.

To handle this, Aider does not rely on a naive `file_text.replace(search, replace)`. Instead, it uses a **multi-tiered matching pipeline**:

1. **Exact Match:**
   Attempts a direct substring match. If it appears exactly once, it applies the replacement immediately.
2. **Whitespace/Indentation Flexibility:**
   If exact match fails, Aider normalizes line endings (`\r\n` vs `\n`), strips trailing whitespace, and attempts to match with normalized indentation.
3. **Similarity / Heuristic Matching (Difflib / Levenshtein):**
   If still not found, Aider splits the target file into sliding windows of lines and calculates similarity ratios against the `SEARCH` block. If a window has a high similarity score (e.g., >95%), it maps the replacement onto that window.
4. **Uniqueness Enforcement:**
   If a `SEARCH` block matches multiple places in the file, Aider **refuses to guess**. A replacement is only applied if the match is unique; otherwise, it stops to avoid corrupting the wrong function.

---

### 4. The Reflection / Self-Correction Loop

When a SEARCH block fails to match (or is ambiguous), Aider does not give up and crash. It feeds the failure back to the LLM automatically in the next turn:

> *"The SEARCH block for `src/auth.py` could not be found. Here is the actual content of lines 40–60 of `src/auth.py`. Please provide a new SEARCH/REPLACE block that accurately matches the file."*

The model almost always corrects the discrepancy on the second attempt.

---

### 5. Multi-Model Architecture ("Architect / Editor" Mode)

For complex tasks, Aider separates the thinking from the formatting:
1. **Architect Model (e.g. o3-mini or Claude 3.7 Thinking):** Discusses the solution, explores the codebase, and outputs high-level instructions and code snippets without worrying about rigid block syntax.
2. **Editor Model (e.g. GPT-4o-mini or a fast local model):** Takes the architect's proposal and the existing file contents, then strictly outputs valid SEARCH/REPLACE blocks.

This prevents smart models from "wasting" reasoning capacity on matching exact indentation tokens.

---

### 6. Safety: Native Git Integration

Aider treats **Git as its undo stack**:
- Every time an agent turn successfully applies changes, Aider automatically creates a Git commit with a descriptive message (`aider: refactor jwt auth to async`).
- If the user doesn't like the result, they run `/undo` in Aider, which executes a simple `git reset --hard HEAD~1`.

---

### How This Compares to Structured Tool Calls (Claude Code / OpenCode)

| Dimension | Aider (SEARCH / REPLACE Blocks) | Structured Tool Calls (`edit_file`) |
| :--- | :--- | :--- |
| **Transport** | Standard markdown streaming text. | JSON function-calling schema (`tools: [...]`). |
| **Model Compatibility** | Works on **any** LLM (even models with poor tool calling). | Requires models fine-tuned for tool calling (Claude 3.5/3.7, GPT-4o). |
| **Streaming Feedback** | Sublime can parse blocks *while streaming*, showing live progress. | JSON arguments stream as raw JSON fragments; parsed only when completed. |
| **Token Efficiency** | Very high. Only modified lines + small context are sent. | Similar if using `{old_str, new_str}`, but slightly more JSON overhead. |
| **Parsing Fragility** | Requires robust regex/heuristic parsing of text blocks. | Rigid JSON parsing (can fail if the model emits unescaped quotes or newlines). |

---

### Relevant Considerations for Sublime Text

If we bring this model into Sublime Text, we have distinct advantages that a CLI like Aider doesn't have:
1. **The Native Buffer:** We don't just have files on disk; we have Sublime `View` objects. If a file is currently open in a tab, edits can be applied directly to the active buffer.
2. **Native Undo History:** By grouping modifications inside a `sublime_plugin.TextCommand` using `view.run_command()`, the user gets immediate `Ctrl+Z` support in the editor tab without requiring a git commit.
3. **Visual Diffs:** Sublime has native inline diff support (mini-diffs in the gutter) and can easily spawn side-by-side diff tabs.

# --- User ---
Is the Aider SEARCH&REPLACE strategy also cost-saving in term of token usage and API-related costs?

# --- Agent ---
**Yes, significantly.** In fact, token and cost efficiency was the primary motivation for creating SEARCH/REPLACE blocks in the first place.

Here is a breakdown of why it saves money, where the savings come from, and where the trade-offs lie.

---

### 1. Output Tokens Are the Most Expensive Tokens

In LLM pricing (OpenAI, Anthropic, DeepSeek, etc.), **output tokens cost between 3x and 5x more than input tokens**.

| Model | Input Price (per 1M) | Output Price (per 1M) | Output vs Input Ratio |
| :--- | :--- | :--- | :--- |
| **Claude 3.7 Sonnet** | $3.00 | $15.00 | **5x** |
| **GPT-4o** | $2.50 | $10.00 | **4x** |
| **DeepSeek-V3** | $0.27 (cache miss) | $1.10 | **4x** |

When modifying a 600-line file:
- **Full file rewrite strategy:** The model must re-generate all 600 lines (~3,000 output tokens) even to change a single variable name.
  - Cost: ~$0.045 per edit on Claude 3.7.
- **SEARCH/REPLACE strategy:** The model outputs only a 5-line search block and a 5-line replacement block (~50 output tokens).
  - Cost: ~$0.00075 per edit.
  - **Result: Over 98% reduction in output token costs for that turn.**

---

### 2. Reduced Latency = Lower Perceived Cost & Faster Workflows

Beyond raw dollars, output generation is bounded by LLM generation speed (tokens per second):
- Emitting **3,000 tokens** (full file) takes **15 to 40 seconds**.
- Emitting **60 tokens** (SEARCH/REPLACE) takes **less than 1 second**.

A single prompt that touches 3 different files can execute almost instantaneously with SEARCH/REPLACE, whereas full file rewriting takes minutes and risks hitting output context limits (e.g. 4096 or 8192 token truncation).

---

### 3. Synergizes with Prompt Caching

Modern models (Anthropic, OpenAI, DeepSeek) offer **Prompt Caching**, which discounts input tokens by **50% to 90%** if the prompt prefix hasn't changed.

Because Aider uses `@file` context injection (similar to what you already have in `FILE_INJECTION.md`):
- The full files are injected as **input context** once and get cached.
- Subsequent edits only emit tiny SEARCH/REPLACE outputs.
- You pay pennies for cached input reads, and fractions of a cent for tiny search/replace output blocks.

---

### 4. When Does SEARCH/REPLACE *Not* Save Tokens? (The Caveats)

There are two scenarios where SEARCH/REPLACE has minor overhead:

1. **Massive Rewrites:**
   If a user asks to *"completely refactor this file from scratch"*, SEARCH/REPLACE actually consumes *more* tokens than a direct write, because the model outputs the old file inside `<<<<<<< SEARCH` **and** the new file inside `======= REPLACE`. (Aider detects this and switches to "whole file" mode for new or heavily rewritten files).
2. **Failed Matches & Retries:**
   If the model makes an indentation error and the SEARCH block fails to match, Aider re-prompts the model with the error. That retry incurs a second round-trip of input + output tokens. (This is why Aider's fuzzy matching engine is crucial—it prevents unnecessary retry turns).

---

### Summary for Your Sublime Text Strategy

For an editor-integrated assistant, SEARCH/REPLACE gives you the best financial and performance profile:
- **Cheapest:** You avoid paying 4x–5x output token rates for unchanged boilerplate code.
- **Fastest:** Edits feel immediate because the LLM only streams small chunks.
- **Safer:** Truncation errors (hitting model token limits mid-file) are virtually impossible because the generated output is minimal.

# --- User ---
What is your plan to implement a file editing functionality, with the Aider's SEARCH&REPLACE technique?
Read @lib/file_injector.py for an example of an existing module into my library to customize my Agentic package.

# --- Agent ---
Based on the existing clean architecture in `lib/file_injector.py`, here is the complete architectural plan for implementing the **Aider SEARCH/REPLACE File Editing Engine** for your `Agentic` package.

---

## 1. System Architecture Overview

```
                      ┌───────────────────────────────┐
                      │  LLM Response / Chat Stream   │
                      └──────────────┬────────────────┘
                                     │
                        (Text with <<<<<<< SEARCH)
                                     ▼
                      ┌───────────────────────────────┐
                      │    lib/patch_parser.py        │
                      │  - Extract blocks             │
                      │  - Target filepath detection  │
                      └──────────────┬────────────────┘
                                     │
                        (FileEdits: search, replace)
                                     ▼
                      ┌───────────────────────────────┐
                      │    lib/patch_engine.py        │
                      │  - Multi-tier fuzzy matching  │
                      │  - Dry-run validation         │
                      │  - Generate new buffer/string │
                      └──────────────┬────────────────┘
                                     │
                        (Validated new content)
                                     ▼
                      ┌───────────────────────────────┐
                      │   lib/sublime_editor.py       │
                      │  - Open / locate View         │
                      │  - Execute TextCommand        │
                      │  - Native Undo checkpoint     │
                      │  - Highlight modified lines   │
                      └───────────────────────────────┘
```

---

## 2. Core Modules to Introduce

We will structure this cleanly alongside `lib/file_injector.py` following the same modular, testable patterns:

### Module 1: `lib/patch_parser.py` (Block Parsing)
Responsible solely for scanning markdown text and producing structured edit objects.

- **Standard Block Grammar:**
  ````markdown
  path/to/file.py
  <<<<<<< SEARCH
  def old_code():
      pass
  =======
  def new_code():
      pass
  >>>>>>> REPLACE
  ````
- **Variants Supported:**
  - Filepaths inside code fences, markdown headers (`### path/to/file.py`), or plain text right before the opening marker.
  - Multiple `SEARCH/REPLACE` blocks per file.
  - Multiple files in a single model turn.
- **Output Data Structure:**
  ```python
  @dataclass # or simple dict/NamedTuple compatible with Python 3.8 (Sublime's runtime)
  class FilePatch:
      raw_path: str
      search_block: str
      replace_block: str
  ```

---

### Module 2: `lib/patch_engine.py` (The Matching Engine)
The hardest and most critical part: matching the LLM's `SEARCH` block against actual file content on disk/buffer. It uses a **3-tier matching strategy**:

1. **Tier 1: Exact Substring Match**
   - Direct match check (`content.count(search) == 1`).
   - If count > 1, fails with `AmbiguousMatchError` (never guess when ambiguous).
2. **Tier 2: Whitespace & Line-Ending Normalization**
   - Normalizes line endings (`\r\n` -> `\n`).
   - Strips trailing whitespace per line.
   - Normalizes leading indentation relative to the block's base indent (handles tab/space mismatches).
3. **Tier 3: Sliding-Window Heuristic (Fuzzy Match)**
   - Uses Python's built-in `difflib.SequenceMatcher`.
   - Slides a window across the file lines matching the line count of the search block ($\pm 2$ lines).
   - If a single window scores $\ge 0.95$ similarity and no other window is close, matches and applies the replacement onto that window.
4. **Safety Verification:**
   - Dry-run before applying anything. If a file has 3 blocks, and the 3rd block fails to match, **zero** modifications are written to that file (all-or-nothing transactional edits).

---

### Module 3: `lib/sublime_editor.py` (Sublime Text Native Bridge)
Translates the computed patch into actual Sublime Text buffer actions, giving you full IDE integration:

- **Buffer-Aware Editing:**
  - Checks if the file is already open in a tab (`window.find_open_file(filepath)`).
  - If open: edits the active `View` in memory. The user sees the code change immediately, with Sublime's "dirty tab" indicator (`●`).
  - If closed: opens the file in the background or applies the edit directly.
- **Native Undo / Redo Integration:**
  - Implements a dedicated `sublime_plugin.TextCommand` (e.g., `agentic_apply_patch`).
  - All replacements for a file occur within a single command execution, meaning **one `Ctrl+Z` undoes the entire agent turn for that file**.
- **Visual Feedback:**
  - Adds temporary status-bar notifications: `Agentic: Applied 2 edits to src/auth.py`.
  - Optionally uses `view.add_regions()` to temporarily highlight modified lines with a subtle background tint (cleared upon user click or save).

---

## 3. The LLM System Prompt Strategy

To make models reliably output this exact format, we inject a concise editing specification into the system instructions when file editing is requested:

```markdown
When editing code, output one or more SEARCH/REPLACE blocks formatted as follows:

path/to/file.ext
<<<<<<< SEARCH
exact lines to be removed
=======
replacement lines
>>>>>>> REPLACE

Critical rules:
1. Provide sufficient surrounding context in the SEARCH block to make it unique.
2. Maintain exact indentation, whitespace, and capitalization.
3. Every SEARCH block must target existing code in the file.
4. Output the relative path to the file immediately before the opening `<<<<<<< SEARCH`.
```

---

## 4. User Interaction & Workflow Modes

We will design this with two execution modes:

### Mode A: Auto-Apply Mode (CLI style - like Aider)
When the LLM finishes streaming its response:
1. Parser scans the assistant message.
2. If SEARCH/REPLACE blocks are detected:
   - Validates paths using your existing `resolve_path()` hierarchy from `file_injector.py`.
   - Runs matching engine.
   - Applies changes to the Sublime buffers.
   - Prints a concise summary in the chat: `✓ Updated src/auth.py (2 blocks)`.

### Mode B: Review / Approval Mode (Optional Safety Guardrail)
Before writing to the buffer:
- Displays a Sublime quick-panel or bottom notification: `Agent proposed 2 changes to auth.py. [Apply] [Diff] [Reject]`.
- Clicking `[Diff]` opens a scratch buffer showing a unified diff of the proposed changes.

---

## 5. Phased Implementation Plan

1. **Phase 1: Unit-Testable Core (Pure Python, No Sublime API dependency)**
   - Build `lib/patch_parser.py`: extract blocks and target filepaths from arbitrary LLM markdown.
   - Build `lib/patch_engine.py`: implement exact matching, whitespace normalization, and fuzzy sliding-window matching.
   - Create comprehensive unit test suite in `tests/test_patch_engine.py` (verifying exact matches, indentation variations, and error reporting on ambiguous blocks).

2. **Phase 2: Sublime Text Integration**
   - Create `lib/sublime_editor.py` with `AgenticApplyPatchCommand` (`TextCommand`).
   - Integrate with `resolve_path()` from `lib/file_injector.py` to support `@` paths and project-relative paths.
   - Wire native buffer updates and undo grouping.

3. **Phase 3: Chat Loop Integration & Feedback**
   - Connect the output of the chat completion handler to trigger the patch pipeline.
   - Handle match failures gracefully: if a block fails to match, format a clear error message in the chat buffer showing what failed to match so the user (or the agent in a loop) can retry.

---

How does this strategic layout look to you? If this aligns with your vision, we can explore any specific design decisions before moving to implementation.

# --- User ---
How would I invoke the agent file-editing functionality?
Would I plain-text prompt for it? e.g.: "Edit this `@path_to_file` to add Doxygen-style documentation to functions foo() and bar()"

Can I add a Sublime Text command to the file @chat_stream.py ?
e.g. `class AgenticEditFileCommand(sublime_plugin.TextCommand)` 
Notice: the command has to accept two arguments, a file path, and a prompt,
or maybe a prompt containing a file reference with the `@` character.

With a command-class defined I can later add support to recognise the slash-style syntax for the command (i.e. `edit ....`), or a keyboard shortcut.

Please, ellaborate on these questions.

# --- Agent ---
Here is an elaboration on both of your questions: **how file editing can be invoked** and **how to structure the Sublime Text command**.

---

### 1. How to Invoke the Agent File-Editing Functionality

In tools like Aider or Claude Code, there are two distinct user workflows. We can support both seamlessly:

#### Workflow A: Direct Conversational Invocation (Inside the Chat View)
You simply chat in your normal Agentic chat buffer (`# --- User ---`) and press `Ctrl+Enter`:
```markdown
# --- User ---
Edit `@ src/models/user.py` to add Doxygen-style documentation to functions foo() and bar().
```
- **How it works:** 
  1. `file_injector.py` detects `@ src/models/user.py` and injects its contents into the prompt in-memory.
  2. Because the agent's system prompt instructions instruct it to output `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks when editing files, the LLM streams back the SEARCH/REPLACE blocks.
  3. When streaming finishes (`_finalize()` in `chat_stream.py`), the patch engine automatically parses the assistant's response, opens `src/models/user.py`, and applies the replacement.

#### Workflow B: Quick-Action Command Palette / Shortcut (`AgenticEditFileCommand`)
You are currently editing a code file (e.g. `src/models/user.py` is your active tab) and press a shortcut (e.g. `Ctrl+Alt+E`) or run `AI Agent: Edit Current File`:
1. An input panel pops up asking: `"Edit prompt: "`.
2. You type: `"Add Doxygen documentation to foo() and bar()"`.
3. Sublime creates a chat (or executes in the background), references the file, runs the model, and applies the patch directly to your active tab.

---

### 2. WindowCommand vs TextCommand: Which One to Use?

In Sublime Text:
- A **`TextCommand`** (`sublime_plugin.TextCommand`) runs *inside* a specific `View` buffer and is used when directly modifying text (using the provided `edit` token).
- A **`WindowCommand`** (`sublime_plugin.WindowCommand`) controls the editor workspace (opening files, prompting the user for input via quick-panels, managing chat tabs, and dispatching streams).

For your high-level command:
- **`AgenticEditFileCommand` should be a `WindowCommand`** (or `TextCommand` if you want it to implicitly know the active view).
- We will have a secondary low-level **`AgenticApplyPatchCommand` (a `TextCommand`)** that strictly performs the atomic text replacement inside the target buffer using Sublime's `view.replace(edit, ...)`.

---

### 3. Concrete Design of `AgenticEditFileCommand`

We can design `AgenticEditFileCommand` to be flexible:
1. It can accept `prompt` and optional `file_path`.
2. If `file_path` is not provided, it automatically defaults to the currently active tab's file (`view.file_name()`).
3. If `prompt` contains `@file` mentions, it respects those as well.

Here is how that looks in conceptual code:

```python
class AgenticEditFileCommand(sublime_plugin.WindowCommand):
    """
    Prompt-driven file editing command.
    Can be run via:
      - Command Palette (with input prompt)
      - Keyboard shortcut
      - API / Python: window.run_command("agentic_edit_file", {"file_path": "...", "prompt": "..."})
    """
    def run(self, prompt=None, file_path=None):
        view = self.window.active_view()

        # Fallback to the active view's file if no file_path specified
        if not file_path and view and view.file_name():
            file_path = view.file_name()

        if not prompt:
            # If prompt not provided, open Sublime's bottom input panel
            self.window.show_input_panel(
                "Edit Prompt for {}:".format(os.path.basename(file_path) if file_path else "file"),
                "",
                lambda user_prompt: self._execute_edit(user_prompt, file_path),
                None,
                None
            )
            return

        self._execute_edit(prompt, file_path)

    def _execute_edit(self, prompt, file_path):
        if not prompt:
            return

        # If file_path is known and prompt doesn't already reference it, prefix it
        if file_path and ("@ " + os.path.basename(file_path)) not in prompt and ("@ " + file_path) not in prompt:
            full_prompt = "Edit @{}\n{}".format(file_path, prompt)
        else:
            full_prompt = prompt

        # We construct the chat with the file-editing system prompt
        # and launch the streaming task
        ...
```

---

### 4. Supporting Slash-Command Syntax (`/edit`) Later

Because the command is registered as `agentic_edit_file`, introducing a slash command in your chat buffer later is straightforward.

Inside `AgenticChatCommand.run()`, when the user presses `Ctrl+Enter`, we inspect the last user turn. If the user typed:
```markdown
# --- User ---
/edit src/auth.py Add docstrings to all functions
```
The pre-dispatch router can intercept `/edit`:
1. Parse the slash command: command name = `edit`, args = `src/auth.py`, prompt = `Add docstrings...`.
2. Dispatch to `self.window.run_command("agentic_edit_file", {"file_path": ..., "prompt": ...})`.

---

### 5. Summary of the Flow

```
User Action:
  - Chat buffer: "Refactor @ auth.py to..." [Ctrl+Enter]
  - OR Shortcut/Palette: "agentic_edit_file"
           │
           ▼
 chat_stream.py (start_streaming)
   1. file_injector.py resolves and reads @ auth.py
   2. chat_stream() attaches Aider SEARCH/REPLACE system instructions
   3. Model streams <<<<<<< SEARCH ... >>>>>>> REPLACE
           │
           ▼
 On Stream Completion (_finalize):
   1. lib/patch_parser.py extracts blocks & file targets
   2. lib/patch_engine.py matches and verifies lines
   3. Target view runs `agentic_apply_patch` (TextCommand)
   4. User sees the updated tab with full Ctrl+Z (Undo) history!
```

# --- User ---
OK -- I judge the design phase is complete.
How can we proceed to implement?
You can write to me textually the entire files to be added (alogside their locations), or the blocks of text or new functions/classes/methods to be added to existing files.


# --- Agent ---
Here is the complete implementation step-by-step. 

We will create two standalone library modules under `lib/`, a test suite to ensure the matching engine is robust, and then make targeted updates to `chat_stream.py` to wire everything together.

---

### File 1: `lib/patch_parser.py` (New File)
**Path:** `/home/yoel/.config/sublime-text/Packages/Agentic/lib/patch_parser.py`

This module extracts `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` blocks and associates each block with its target filepath from LLM Markdown output.

```python
# Packages/Agentic/lib/patch_parser.py
"""
Agentic Patch Parser Library

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
```

---

### File 2: `lib/patch_engine.py` (New File)
**Path:** `/home/yoel/.config/sublime-text/Packages/Agentic/lib/patch_engine.py`

This module implements the multi-tier matching engine (Exact -> Whitespace-Normalized -> Sliding Window Fuzzy Match) and transactionally computes updated file text.

```python
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


def _find_fuzzy_window(content_lines, search_lines, min_similarity=0.92):
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
    second_best_ratio = 0.0

    # Allow slight flexibility in window size (+/- 2 lines)
    for win_len in range(max(1, search_len - 2), min(len(content_lines) + 1, search_len + 3)):
        for i in range(len(content_lines) - win_len + 1):
            window_str = "\n".join(_normalize_line(l) for l in content_lines[i:i + win_len])
            matcher = difflib.SequenceMatcher(None, search_str, window_str)
            ratio = matcher.quick_ratio()

            if ratio >= min_similarity:
                real_ratio = matcher.ratio()
                if real_ratio > best_ratio:
                    second_best_ratio = best_ratio
                    best_ratio = real_ratio
                    best_start = i
                    best_end = i + win_len
                elif real_ratio > second_best_ratio:
                    second_best_ratio = real_ratio

    # Must be unambiguous: best match must be >= min_similarity and significantly better than 2nd best
    if best_ratio >= min_similarity and (best_ratio - second_best_ratio) > 0.05:
        return best_start, best_end

    return None


def apply_single_block(current_content, search_text, replace_text):
    """
    Applies a single SEARCH/REPLACE block to current_content.
    Returns the new updated string, or raises PatchError.
    """
    # 1. Exact match attempt
    match_span = _find_exact(current_content, search_text)
    if match_span:
        start, end = match_span
        return current_content[:start] + replace_text + current_content[end:]

    # Detect line endings
    crlf = "\r\n" in current_content
    newline = "\r\n" if crlf else "\n"

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
```

---

### File 3: `tests/test_patch_engine.py` (New Unit Tests)
**Path:** `/home/yoel/.config/sublime-text/Packages/Agentic/tests/test_patch_engine.py`

This ensures full unit test coverage for exact matching, whitespace variances, fuzzy window detection, and block parsing without needing Sublime running.

```python
# Packages/Agentic/tests/test_patch_engine.py
import unittest
import sys
import os

# Ensure package root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lib.patch_parser import parse_patch_blocks
from lib.patch_engine import apply_patch_blocks, apply_single_block, PatchError


class TestPatchEngine(unittest.TestCase):

    def test_parse_single_block(self):
        sample = """
Here is the fix for `src/auth.py`:

src/auth.py
<<<<<<< SEARCH
def login():
    return False
=======
def login():
    return True
>>>>>>> REPLACE
"""
        blocks = parse_patch_blocks(sample)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].filepath, "src/auth.py")
        self.assertEqual(blocks[0].search_text.strip(), "def login():\n    return False")
        self.assertEqual(blocks[0].replace_text.strip(), "def login():\n    return True")

    def test_exact_match_replace(self):
        content = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        search = "def foo():\n    return 1\n"
        replace = "def foo():\n    return 42\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("return 42", res)
        self.assertIn("def bar():", res)

    def test_whitespace_tolerance(self):
        # Target file has trailing spaces, search block does not
        content = "def calculate():   \n    x = 10  \n    return x\n"
        search = "def calculate():\n    x = 10\n    return x"
        replace = "def calculate():\n    x = 20\n    return x\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("x = 20", res)

    def test_fuzzy_matching(self):
        # Slight line comment deviation
        content = "def run():\n    # old comment\n    setup()\n    execute()\n"
        search = "def run():\n    # comment changed slightly\n    setup()\n    execute()"
        replace = "def run():\n    setup()\n    execute()\n    cleanup()\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("cleanup()", res)

    def test_ambiguous_block_fails(self):
        content = "item = 1\nitem = 1\n"
        search = "item = 1\n"
        replace = "item = 2\n"
        with self.assertRaises(PatchError):
            apply_single_block(content, search, replace)


if __name__ == "__main__":
    unittest.main()
```

---

### File 4: Updates to `chat_stream.py`

Now we hook the engine directly into Sublime Text in `chat_stream.py`.

#### 1. Add Imports at the Top of `chat_stream.py`:
Add the imports right below `from lib.file_injector import process_messages_file_mentions`:

```python
try:
    from .lib.file_injector import process_messages_file_mentions, resolve_path
    from .lib.patch_parser import parse_patch_blocks
    from .lib.patch_engine import apply_patch_blocks
except (ImportError, ValueError):
    from lib.file_injector import process_messages_file_mentions, resolve_path
    from lib.patch_parser import parse_patch_blocks
    from lib.patch_engine import apply_patch_blocks
```

#### 2. Add the Native Undo / Buffer Command:
Add this `TextCommand` anywhere in `chat_stream.py` (e.g. above `AgenticEditFileCommand`):

```python
class AgenticApplyBufferPatchCommand(sublime_plugin.TextCommand):
    """
    Replaces entire buffer content with patched content in a single atomic undo step.
    """
    def run(self, edit, new_content=""):
        region = sublime.Region(0, self.view.size())
        self.view.replace(edit, region, new_content)
```

#### 3. Add the Core Patch Execution Function:
Add this helper to handle locating open tabs or opening background files, checking patches, and executing them:

```python
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
        resolved = resolve_path(block.filepath, window=window)
        if not resolved:
            return ["Error: Cannot resolve file path '{}'".format(block.filepath)]
        grouped.setdefault(resolved, []).append(block)

    results = []
    for filepath, file_blocks in grouped.items():
        # Check if already open in Sublime
        view = window.find_open_file(filepath)
        if view and view.is_loading():
            # Wait briefly or fall back
            time.sleep(0.1)

        if view and view.is_valid():
            original_content = view.substr(sublime.Region(0, view.size()))
            success, new_content, err = apply_patch_blocks(original_content, file_blocks)
            if success:
                view.run_command("agentic_apply_buffer_patch", {"new_content": new_content})
                results.append("✓ Updated {} in editor ({} block{})".format(
                    os.path.basename(filepath), len(file_blocks), "s" if len(file_blocks) > 1 else ""))
            else:
                results.append("✗ Failed to patch {}: {}".format(os.path.basename(filepath), err))
        else:
            # File is on disk; read, modify, and open in Sublime
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    original_content = f.read()

                success, new_content, err = apply_patch_blocks(original_content, file_blocks)
                if success:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    window.open_file(filepath)
                    results.append("✓ Updated {} on disk ({} block{})".format(
                        os.path.basename(filepath), len(file_blocks), "s" if len(file_blocks) > 1 else ""))
                else:
                    results.append("✗ Failed to patch {}: {}".format(os.path.basename(filepath), err))
            except Exception as e:
                results.append("✗ Error reading {}: {}".format(os.path.basename(filepath), str(e)))

    return results
```

#### 4. Trigger Patch Application in `AgentStreamingTask._finalize`:
In `AgentStreamingTask` inside `chat_stream.py`, update `_finalize()` so that when streaming finishes, any SEARCH/REPLACE blocks in the conversation turn are automatically detected and applied:

```python
    def _finalize(self):
        if not self.is_valid():
            return
        self.registry.pop(self.view.id(), None)

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
```

#### 5. Add the `AgenticEditFileCommand`:
Add the new window command to `chat_stream.py`:

```python
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

        if not prompt:
            filename = os.path.basename(file_path) if file_path else "file"
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
        if file_path:
            rel = file_path
            for folder in self.window.folders():
                if file_path.startswith(folder):
                    rel = os.path.relpath(file_path, folder)
                    break
            user_msg = "Please edit @{}\n\nInstruction: {}".format(rel, prompt)
        else:
            user_msg = prompt

        # Create system prompt containing the SEARCH/REPLACE rules
        default_system = sublime.load_settings("Agentic.sublime-settings").get("default_prompt", "")
        system_content = default_system + "\n" + EDIT_SYSTEM_INSTRUCTIONS.strip()

        chat_text = "# --- System ---\n{}\n\n# --- User ---\n{}\n".format(system_content, user_msg)
        chat_view = _create_chat(self.window, "Edit " + (os.path.basename(file_path) if file_path else "File"), chat_text)

        messages = _build_messages_from_text(chat_text)
        start_streaming(chat_view, messages)
```

---

### Verification and Testing

1. **Run Unit Tests:**
   ```bash
   python3 -m unittest discover -s ~/.config/sublime-text/Packages/Agentic/tests
   ```
2. **Try It in Sublime Text:**
   - Open any code file in a tab.
   - Run in the Sublime Text Console (`Ctrl + \``):
     ```python
     window.run_command("agentic_edit_file", {"prompt": "Add docstrings to all functions"})
     ```
   - Watch the agent stream the SEARCH/REPLACE blocks. When done, your original code buffer will update with a single undo step (`Ctrl+Z`), and the chat view will display:
     ```markdown
     **File Edits Applied:**
      - ✓ Updated auth.py in editor (1 block)
     ```

# --- User ---
```text
======================================================================
ERROR: test_fuzzy_matching (tests.test_patch_engine.TestPatchEngine.test_fuzzy_matching)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/yoel/.config/sublime-text/Packages/Agentic/tests/test_patch_engine.py", line 55, in test_fuzzy_matching
    res = apply_single_block(content, search, replace)
  File "/home/yoel/.config/sublime-text/Packages/Agentic/lib/patch_engine.py", line 133, in apply_single_block
    raise PatchError("Could not locate SEARCH block in target file. First line: '{}'".format(first_line))
lib.patch_engine.PatchError: Could not locate SEARCH block in target file. First line: 'def run():'

----------------------------------------------------------------------
Ran 17 tests in 0.019s

FAILED (errors=1)
```

Read my actual, modified @chat_stream.py if you need reference.


# --- Agent ---
The test failed for two reasons in `lib/patch_engine.py`:

1. **Threshold is too high for short snippets:** In a 4-line snippet, modifying even a short comment (`# old comment` $\rightarrow$ `# comment changed slightly`) drops `difflib.SequenceMatcher.ratio()` down to ~`0.83`, which was below `min_similarity = 0.92`. A safer default threshold for code line blocks is `0.80`.
2. **Overlapping window collision (the bug in `second_best_ratio`):** The sliding window checks lengths `search_len - 2` through `search_len + 2`. A window of length 3 and length 4 over the *same* starting line `i=0` will both score high (e.g., `0.81` and `0.84`). The code recorded the length-3 window as `second_best_ratio`, causing `best_ratio - second_best_ratio < 0.05` to falsely treat different boundaries of the *same* location as an ambiguous collision!

---

### The Fix

Here is the corrected `lib/patch_engine.py`. Replace the contents of `/home/yoel/.config/sublime-text/Packages/Agentic/lib/patch_engine.py` with this:

```python
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
```

---

### Run the tests again:
```bash
python3 -m unittest discover -s ~/.config/sublime-text/Packages/Agentic/tests
```

All 17 tests should now pass cleanly.

# --- User ---
Can this plugin be used to _create_ a new file?
Example: Create a Hello World file called `@@hello.py` ?

# --- Agent ---
**Right now, not quite yet**—if you tried it this second, it would fail for two specific reasons:

1. **Pre-flight Check Blocks Non-Existent `@file` Mentions:**
   In `lib/file_injector.py`, referencing `@hello.py` triggers the safety check. Since `hello.py` doesn't exist on disk yet, it halts the request with an *"Unresolved File Reference"* modal so you don't waste API tokens.
   *(Note: writing `@@hello.py` escapes the mention, so pre-flight ignores it, which solves this part!)*

2. **`resolve_path` Requires the File to Exist:**
   Inside `apply_parsed_patches()`, `resolve_path()` checks `os.path.isfile(candidate)`. If the file doesn't exist anywhere in the project, it aborts with `Cannot resolve file path 'hello.py'`.

---

### How Aider Handles New Files (The Convention)

Aider uses the exact same SEARCH/REPLACE format for creating new files, but with an **empty SEARCH block**:

````markdown
hello.py
<<<<<<< SEARCH
=======
def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
>>>>>>> REPLACE
````

Because `SEARCH` is empty, it means: *"Replace nothing (empty file) with this entire content."*

---

### How We Can Add This to Agentic

To support file creation cleanly, we only need three minor improvements:

#### 1. Target Path Resolver for New Files
If `resolve_path` finds an existing file, use it. If not, resolve the new file's destination relative to:
- The first open project folder (`window.folders()[0]`), or
- The directory of the active file (`os.path.dirname(active_view.file_name())`).

#### 2. Teach the Engine to Handle Empty Files / Empty Search
In `apply_patch_blocks`:
- If the target file doesn't exist on disk, initialize `original_content = ""`.
- If `search_text.strip() == ""`, treat the replacement as the full file contents.
- Create parent directories automatically (`os.makedirs(os.path.dirname(path), exist_ok=True)`).
- Open the newly created file in Sublime Text as a new tab so you see it immediately.

#### 3. Update the System Prompt
Inform the model:
```markdown
To create a new file, output an empty SEARCH section:

path/to/new_file.py
<<<<<<< SEARCH
=======
<entire contents of new file>
>>>>>>> REPLACE
```

---

### Would You Like to Enable This?

If you'd like to support creating files, we can update `lib/patch_engine.py` and `chat_stream.py` with these rules. 

You would then be able to prompt:
> *"Create a new file `hello.py` with a Hello World script."*
> (or use `@@hello.py` to prevent pre-flight injection)

and Agentic will create the file on disk, populate it, and open it in a Sublime tab.

**File Edits Applied:**
 - Error: Cannot resolve file path 'hello.py'


# --- User ---
