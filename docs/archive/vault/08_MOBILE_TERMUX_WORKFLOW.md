# 08 — Mobile Termux Engineering Runbook

> **Platform:** Android Termux (Linux aarch64, Bionic Libc)  
> **Toolchain:** Antigravity AI Pair Programmer, Git, Python 3.14, GitHub CLI (`gh`), `tmux`  
> **Active Skills Suite:** 48 Skills installed under `~/.agents/skills/`  

---

## 📱 Mobile Architecture & Optimization

Operating a complex 130k-LOC codebase entirely from a mobile phone requires specific optimizations to guarantee stability, prevent battery-killer kills by Android OS, and maximize productivity.

### 1. Process Persistence (Prevent Background Kills)
Android aggressive memory management will kill Termux if left idle. Run:

```bash
termux-wake-lock
```
*Also ensure Android Settings → Apps → Termux → Battery is set to **Unrestricted**.*

---

### 2. Multi-Window Terminal Workflow (`tmux`)

Do not exit Antigravity to run Git commands or inspect logs. Use `tmux` sessions:

```bash
pkg install tmux -y
tmux new -s work
```

**Essential Shortcuts:**
- `Ctrl+b` then `c`: Open a new terminal window (Window 0 for Antigravity, Window 1 for Git/Bash).
- `Ctrl+b` then `n`: Switch between active windows.
- `Ctrl+b` then `"`: Split terminal horizontally.
- `Ctrl+b` then `%`: Split terminal vertically.
- `Ctrl+b` then `d`: Detach session safely (work continues in background).

---

### 3. Developer Keyboard Layout

To type `Tab`, `Ctrl`, `Esc`, `|`, and arrows effortlessly without fighting standard soft keyboards:

```bash
mkdir -p ~/.termux
cat << 'EOF' > ~/.termux/termux.properties
extra-keys = [['ESC','/','-','HOME','UP','END','PGUP'],['TAB','CTRL','ALT','LEFT','DOWN','RIGHT','PGDN']]
EOF
termux-reload-settings
```

---

### 4. Git & GitHub CLI Management

GitHub CLI (`gh`) is fully authenticated and configured:
- **Authenticated Account**: `SHAURYASANYAL3`
- **Scopes**: `gist`, `read:org`, `repo`, `workflow`
- **Repository Permission**: `WRITE` (Maintainer/Admin) on `Shinraya-Tech-Labs/AgentWatch`

```bash
# Check GitHub authentication
gh auth status

# Check live CI jobs on current branch
gh pr checks 728

# Create and push a new branch
git checkout -b feat/your-feature
git push -u origin feat/your-feature
```

---

### 5. Mobile Skill Disciplines (`~/.agents/skills/`)

All 48 skills are pre-configured to guide Antigravity:
- **`ponytail`**: Enforces minimal diffs, zero boilerplate, and native standard libraries to save CPU, memory, and tokens.
- **`agent-reach`**: Fetches external live intelligence, documentation, and web sources directly without paid APIs. Run `agent-reach doctor` to verify active channels.
- **`python-patterns` & `tdd-workflow`**: Ensures every new algorithm is accompanied by clean, deterministic unit tests before merging.
- **`verification-loop`**: Automatically validates formatting, type safety, and security scans before pushing commits.
