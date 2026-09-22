# Command Reference: `inject_file`, `edit`, `create`, and `inspect`

This document provides a concise reference for using the `inject_file`, `edit`, `create`, and `inspect` actions via both the Command Palette and slash-commands.

---

## 1. `inspect`

Recursively scans a target directory, automatically discovers and selects the files most relevant to your inquiry or task using an intelligent discovery phase, and injects them directly into conversation context.

### Usage

- **Slash-Command:**
  ```text
  /inspect folder @<folder_path> <inquiry>
  ```
  *or simply:*
  ```text
  /inspect @<folder_path> <inquiry>
  ```
  *Examples:*
  ```text
  /inspect folder @my_libs and detect the files that are likely causing this trouble ...
  ```
  ```text
  /inspect @src/auth find where session tokens are refreshed and validated
  ```

- **Command Palette:**
  1. Open the Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`).
  2. Search for and select: `Agentic: Inspect Folder`.
  3. Select or type the folder path.
  4. Enter your question, bug report, or inspection request.

### Configuration (`Agentic.sublime-settings`)

You can control inspection limits and confirmation prompts in your user settings:
```json
{
  // Maximum number of files scanned in directory tree (default: 500)
  "inspect_max_scanned_files": 500,

  // Maximum candidate files selected by the model (default: 6)
  "inspect_max_candidates": 6,

  // Prompt confirmation if candidate files count is >= threshold (set 0 to disable)
  "inspect_confirm_file_count_threshold": 5,

  // Prompt confirmation if total combined size in KB is >= threshold (set 0 to disable)
  "inspect_confirm_total_kb_threshold": 100,

  // Additional file patterns to ignore during folder inspection
  "inspect_ignore_patterns": [
    "*.log",
    "vendor/*"
  ]
}
```

---

## 2. `inject_file`

Injects the contents of one or more existing files into the current context/prompt for the model to reference.

### Usage

- **At-Command:**
  ```text
  @path/to/file
  ```
  *Example:*
  ```text
  Add Doxygen-style documentation to functions in @helpers.py
  ```

- **Command Palette:**
  1. Open the Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`).
  2. Search for and select: `Inject File` (or `Add File to Context`).
  3. Select or type the relative path of the file to include.

---

## 2. `edit`

Modifies an existing file based on instructions or selected text/diffs.

### Usage

- **Slash-Command:**
  ```text
  /edit <path/to/file> <instructions>
  ```
  *Example:*
  ```text
  /edit src/index.ts Add error handling to the startServer function
  ```
  *(If run with an active file open or text selected, the file path can often be omitted or inferred).*

- **Command Palette:**
  1. Open the target file in the editor (and optionally select the code to modify).
  2. Open the Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`).
  3. Search for and select: `Edit` (or `Edit File` / `Edit Selection`).
  4. Enter your prompt/instructions describing the required changes.

---

## 3. `create`

Generates a new file with the specified path and content generated from your prompt.

### Usage

- **Slash-Command:**
  ```text
  /create <path/to/new-file> <instructions>
  ```
  *Example:*
  ```text
  /create tests/auth.test.ts Write unit tests for login and logout handlers
  ```

- **Command Palette:**
  1. Open the Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`).
  2. Search for and select: `Create File` (or `New File with Prompt`).
  3. Provide the destination path and filename.
  4. Enter the prompt describing what the file should contain.

---

## Summary Cheat Sheet

| Action | Slash-Command | Command Palette Action | Purpose |
| :--- | :--- | :--- | :--- |
| **Inspect Folder** | `/inspect [@folder] <prompt>` | `Agentic: Inspect Folder` | Scans directory, chooses relevant files, and injects them into context |
| **Inject File** | `/inject_file <path>` | `Inject File` | Adds file content to conversation context |
| **Edit** | `/edit [path] <instructions>` | `Edit` / `Edit File` | Modifies an existing file or selection |
| **Create** | `/create <path> <instructions>` | `Create File` | Creates a new file from instructions |
