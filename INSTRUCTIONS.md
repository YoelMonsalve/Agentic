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
