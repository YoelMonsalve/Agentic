# Agentic: Automatic File Injection (`@file` Syntax)

## 1. Overview

Agentic now supports modern, terminal-IDE style **in-memory file context injection**. You can reference files directly in your prompts using the `@path` syntax.

Instead of physically pasting file contents into your chat view—which clutters your editor with hundreds or thousands of lines—the `@file` syntax allows you to keep your chat buffer clean and readable while the underlying files are automatically read, formatted, and delivered to the LLM behind the scenes.

---

## 2. Syntax & Examples

### Absolute Paths
Starts with a forward slash (`/`):
```markdown
# --- User ---
Please review @/etc/hosts and explain the entries.
```

### Home-Relative Paths (`~`)
Starts with a tilde (`~`), expanded against your `$HOME` directory:
```markdown
# --- User ---
Check @~/.bashrc and suggest shell aliases to speed up git workflows.
```

### Project-Relative Paths
Resolved against root folders currently open in your Sublime Text workspace (`window.folders()`):
```markdown
# --- User ---
Can you refactor @src/auth/jwt.py to use asynchronous token verification?
```

### Paths with Spaces (Quoted Syntax)
Wrap paths containing spaces in double or single quotes:
```markdown
# --- User ---
Compare @"legacy code/old database.sql" with @"src/models/schema.sql".
```

### Multiple File References
You can reference as many files as needed in a single prompt:
```markdown
# --- User ---
Look at @src/api/routes.py and @src/controllers/user.py.
How is the user authentication flow routed between them?
```

---

## 3. How It Works Internally

### In-Memory Attachment
When you press `Ctrl+Enter` (`agentic_chat`), Agentic executes a pre-flight scan:
1. **Extraction:** Scans your prompt for `@path` references.
2. **Resolution:** Resolves each reference to an absolute filesystem path.
3. **Safety Checks:** Verifies file readability, binary status, and size.
4. **Context Formatting:** Reads the file and generates a clean Markdown attachment:
   ````markdown
   ---
   ### Attached Context: `src/auth/jwt.py`
   Path: `/home/yoel/project/src/auth/jwt.py`
   ```python
   <contents of jwt.py>
   ```
   ````
5. **Dispatch:** Appends this block to the API payload in memory. Your Sublime Text editor window remains uncluttered.

### Multi-Turn Context Persistence
As a chat conversation progresses across multiple turns (`User` -> `Agent` -> `User` -> `Agent`), all referenced files in previous turns remain part of the historical context sent to the model. The model remembers every file mentioned earlier in the discussion, and newly referenced files in subsequent turns are added seamlessly.

---

## 4. Path Resolution Hierarchy

When an `@path` reference is encountered, Agentic searches in the following strict order:

| Step | Priority | Description |
| :--- | :--- | :--- |
| **1** | **Absolute Path** | Path starts with `/` (e.g. `@/var/log/syslog`). Verified directly. |
| **2** | **Home Directory (`~`)** | Path starts with `~` (e.g. `@~/scripts/deploy.sh`). Expanded via `$HOME`. |
| **3** | **Project Root Folders** | Relative path checked against all folders open in the Sublime Text sidebar. |
| **4** | **Active View Directory** | Relative path checked against parent directories of currently open tabs. |
| **5** | **Sublime Working Directory** | Fallback relative to the process working directory (`os.getcwd()`). |

If none of these locations point to an existing regular file, resolution fails.

---

## 5. Safety & Guardrails

### 1. Pre-Flight Halting (Zero Silent Failures)
If **any** referenced file cannot be found or read:
- The request is **immediately halted** before making an API call.
- A Sublime Text modal dialog alerts you to exactly which files could not be resolved.
- No tokens or API costs are incurred, and your chat buffer is left unchanged so you can fix the path.

### 2. Binary File Rejection
Agentic checks the initial bytes of referenced files for null bytes (`\x00`). Compiled binaries, images, PDFs, archives, and executables are rejected to avoid polluting the prompt with undecodable binary noise.

### 3. File Size Boundary (1 MB)
A safe ceiling of 1 MB per file is enforced. Files exceeding 1 MB are blocked to prevent accidentally exceeding model token context limits or exhausting API credit.

### 4. Code Decorator Protection
Unquoted tokens without slashes or file extensions (e.g., Python decorators like `@property`, `@classmethod`, `@staticmethod`, or TypeScript decorators) are automatically ignored by the file scanner, ensuring that existing code snippets pasted into chat do not trigger accidental file searches.

---

## 6. Best Practices

- **Targeted References:** Reference only the files necessary for the prompt. Every injected file increases token consumption and API costs.
- **Deduplication:** Referencing the same file multiple times in one prompt (e.g. *"In @file.py, does @file.py use..."*) only attaches the content once.
- **Inspect Context in Bottom Bar:** Agentic's bottom status bar displays the active context consumption (in tokens and percentage of window limit) as soon as streaming begins. Use this to keep an eye on model limits.
- **Prefer Project-Relative Paths:** Whenever working inside a Sublime Text workspace, use relative paths (e.g. `@src/server.py`) instead of hardcoded absolute paths so chats remain portable.

---

## 7. What to Avoid

- **Do not reference massive generated files:** Avoid pointing `@` at `package-lock.json`, minified bundles (`bundle.min.js`), build outputs, or large dataset CSVs.
- **Do not delete or rename files mid-chat:** Because Agentic re-evaluates file references across conversation turns from your project tree, renaming or deleting a file between prompt turns may cause subsequent turns to trigger an unresolved file notice.
- **Do not use `@` inside backticks if you don't want it resolved:** If you want to discuss the syntax `@path` with the AI without injecting a file, wrap it in inline code fences: \`@path\`.

---

## 8. Running the Unit Tests

The package includes a self-contained unit test suite in `tests/test_file_injector.py` covering path resolution, spaces in filenames, multi-file extraction, decorator protection, binary file rejection, size limits, and deduplication.

### From anywhere in the terminal (Discovery Mode)
Use Python's standard `unittest` test discovery with `-s`:
```bash
# Standard test run:
python3 -m unittest discover -s ~/.config/sublime-text/Packages/Agentic/tests

# Verbose mode (shows each test name and pass status):
python3 -m unittest discover -v -s ~/.config/sublime-text/Packages/Agentic/tests

# Or run the test script directly:
python3 ~/.config/sublime-text/Packages/Agentic/tests/test_file_injector.py
```

### From within the package directory
```bash
cd ~/.config/sublime-text/Packages/Agentic

# Run via module dot-path:
python3 -m unittest tests.test_file_injector

# Or via relative discovery:
python3 -m unittest discover -v -s tests
```
