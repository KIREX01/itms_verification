# Frontend Agent Collaboration & Safe Git Protocol

> **Target Audience**: UI/UX Designer & Frontend Development Agent  
> **Objective**: Enable simultaneous, conflict-free collaboration between the Frontend Agent and Backend/DevOps Agent without breaking production code, corrupting database models, or causing git merge collisions.

---

## 1. Strict File & Domain Boundaries

To guarantee that both agents can work at the exact same time without breaking each other's work, the repository is strictly partitioned:

| Domain | Frontend Agent Scope | Backend / DevOps Agent Scope |
| :--- | :--- | :--- |
| **Templates** | ✅ Full Ownership (`core/templates/**`, `templates/**`) | 🚫 Read-only / Zero edits |
| **Static Assets** | ✅ Full Ownership (`core/static/core/css/**`, `js/**`, `img/**`) | 🚫 Read-only / Zero edits |
| **Python Code** | 🚫 **STRICTLY PROHIBITED** (No `*.py` edits) | ✅ Full Ownership (`core/**/*.py`, `config/**/*.py`) |
| **Database & Migrations**| 🚫 **STRICTLY PROHIBITED** (No model or migration edits) | ✅ Full Ownership (`models.py`, `migrations/**`) |
| **DevOps & Production**| 🚫 **STRICTLY PROHIBITED** (No systemd, nginx, or ssh actions) | ✅ Full Ownership (EC2, deployments, configs) |
| **Request Ledgers** | 📝 Writes to `docs/BACKEND REQUEST.MD` | 📝 Writes to `docs/FRONTEND REQUEST.MD` |

---

## 2. Safe Git Staging & Commit Workflow

Follow this **step-by-step checklist** for every single change:

### Step 1: NEVER Stage Everything Blindly
🚫 **STRICTLY FORBIDDEN**:
```bash
git add .          # DO NOT RUN
git add -A         # DO NOT RUN
git commit -a      # DO NOT RUN
```
*Why? Running `git add .` stages backend files, temporary scratch scripts, or environment variables that the backend agent might be editing.*

### Step 2: ALWAYS Stage Explicit Files
✅ **CORRECT WAY**:
```bash
git add core/templates/core/mobile_companion.html
git add core/static/core/js/mobile_companion.js
git add core/static/core/css/mobile_companion.css
```

### Step 3: Inspect What You Staged Before Committing
Always run:
```bash
git status
git diff --staged --stat
```
Verify that **ONLY** `.html`, `.css`, or `.js` files are in the staged area (Changes to be committed). If you see any `.py` file staged, unstage it immediately:
```bash
git restore --staged <file.py>
```

### Step 4: Write Conventional Commit Messages
Use structured conventional commit messages:
- `feat(ui): add replace image modal to queue table`
- `fix(css): adjust mobile companion shutter button contrast`
- `style(theme): modernize ugandan flag header ribbon styling`
- `refactor(js): extract outbox queue storage into helper service`

### Step 5: Pull Rebase Before Pushing
To avoid messy merge commits when both agents are committing to `main`:
```bash
git fetch origin
git pull --rebase origin main
git push origin main
```

### Step 6: Forbidden Destructive Git Commands
The following commands are strictly prohibited because they can destroy working code or wipe remote repositories:
- 🚫 `git push --force` or `git push -f`
- 🚫 `git reset --hard`
- 🚫 `git clean -f`

---

## 3. Cache-Busting Rule for CSS & JavaScript

Browsers (especially mobile devices) aggressively cache `.css` and `.js` files. 

Whenever you modify any file in `core/static/core/css/` or `core/static/core/js/`:
1. Find where that asset is included in the template.
2. Increment the `?v=` version query parameter by 1.

**Example**:
```html
<!-- BEFORE -->
<link rel="stylesheet" href="{% static 'core/css/mobile_companion.css' %}?v=16">
<script src="{% static 'core/js/mobile_companion.js' %}?v=16"></script>

<!-- AFTER EDITING -->
<link rel="stylesheet" href="{% static 'core/css/mobile_companion.css' %}?v=17">
<script src="{% static 'core/js/mobile_companion.js' %}?v=17"></script>
```

---

## 4. How to Request Backend Endpoints & Data

If your UI requires new data, a new API endpoint, or an extra field:
1. **DO NOT edit Django views or Python models yourself.**
2. Open [`docs/BACKEND REQUEST.MD`](./BACKEND%20REQUEST.MD).
3. Add a new request entry following this structure:
   ```markdown
   ## Feature: <Feature Name>
   ### Endpoint: <Short Description>
   - **URL**: `POST /api/.../` or `GET /api/.../`
   - **Request Payload**: What JSON or FormData the frontend sends
   - **Expected Response**: Sample JSON structure needed by the frontend
   - **Priority**: Critical / High / Medium / Low
   ```
4. The Backend Developer will implement the endpoint, test it with automated unit tests, deploy it to the production server, and mark the item `[COMPLETED]` with the commit hash in `docs/BACKEND REQUEST.MD`.

---

## 5. How to Check for Backend Requests

Regularly check [`docs/FRONTEND REQUEST.MD`](./FRONTEND%20REQUEST.MD).  
The Backend Developer logs frontend refactoring notes, template context requirements, and API envelope contracts there for you to bind into the UI.
