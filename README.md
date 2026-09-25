# deploy_master
Deploy your Python project to shared hosting quickly and easily

![deploy_master](/images/deploy_master.png)

## Description

Lightweight deployment tool for Python applications on shared hosting (ISPmanager, cPanel) via SSH/SCP with MD5 sync and app restart.

## Features

- **Robust Deployment Modes**: Includes safe and forced modes (`UPDATE`, `REWRITE`, `CLEAR` and their `_FORCE` counterparts).
- **Gitignore Compatibility**: Uses `pathspec` to perfectly match `.gitignore` standards natively.
- **Server File Protection**: Safely ignore specific files strictly on the remote server (e.g., SQLite databases, root configs) preventing accidental deletion or overwrite.
- **Conditional Post-Deploy Hooks**: Run `ACTION_AFTER` and `ACTION_CHECK` to reboot apps automatically based on `ACTION_CONDITION` (`ALWAYS`, `ON_UPDATE`, `ON_CLEAR`, `NEVER`).
- **Interactive Preview**: Use the `--preview` flag to review proposed additions, modifications, and deletions before confirming.
- **Colorful CLI**: Beautiful colored output identifying what gets deleted (Red), added (Green), or preserved (Orange/Gray).
- **Secure Keyring**: OS-level password management with interactive prompt fallback.

---

## Installation

This project is built and managed using [`uv`](https://github.com/astral-sh/uv).

Clone the repository and install dependencies:

```bash
git clone [https://github.com/mr-js.ru/deploy_master.git](https://github.com/mr-js.ru/deploy_master.git)
cd deploy_master
uv sync
```

*(Note: Ensure you have `uv` installed on your machine).*

---

## Configuration (`sample_deploy_config.py`)

Create a deployment configuration file in your project directory:

```python
HOST = "37.140.192.75"
PORT = 22
USERNAME = "deployer"
SERVICE = "my_hosting"
SOURCE = r"C:\Users\User\Projects\my_app"
DESTINATION = r"/var/www/my_app"

# Configuration specific to deployment logic
MODE = "UPDATE"
PREVIEW = True  # Enable interactive terminal preview

# Project-specific ignored files locally
CUSTOM_IGNORED_LOCAL = ["*.zip"]

# Protect remote files from deletion or overwrites. 
# Prepend with '/' to restrict the mask to the root directory only.
CUSTOM_IGNORED_SERVER = [
    "/.htaccess",
    "/passenger_wsgi.py",
    "/google*.html",
    "/yandex_*.html"
]

# Post-deploy actions condition (NEVER, ALWAYS, ON_CLEAR, ON_UPDATE)
ACTION_CONDITION = "ON_UPDATE"
ACTION_AFTER = 'touch ".restart-app" && echo "Restart triggered"'
ACTION_CHECK = 'for i in {1..60}; do if [ ! -f ".restart-app" ]; then echo "OK!"; exit 0; fi; sleep 1; done; echo "Server not started"; exit 1'
```

### Deployment Modes (`--mode`)

| Mode | Description |
| :--- | :--- |
| `UPDATE` | **(Default)** Smart sync. Uploads only modified/new files. Remote files not present locally are preserved. Skips files protected by SERVER rules. |
| `UPDATE_FORCE` | Same as `UPDATE`, but overrides SERVER protection rules. |
| `REWRITE` | Forcibly uploads all local files (ignores MD5). Preserves remote-only files. Skips SERVER protected files. |
| `REWRITE_FORCE` | Same as `REWRITE`, but overrides SERVER protection rules. |
| `CLEAR` | Wipes the remote destination first, then uploads all files. Keeps files protected by SERVER rules. |
| `CLEAR_FORCE` | Nuclear option. Completely wipes all contents in the remote destination ignoring SERVER protection, then uploads. |

---

## Usage Methods

### 1. Using `uv run` (Recommended)

Run directly through `uv` without manually managing virtual environments:

```bash
uv run deploy-master --configfile sample_deploy_config.py
```

### 2. Overriding Flags via CLI

You can pass or override any configuration parameter via CLI:

```bash
uv run deploy-master \
  --host 192.0.2.1 \
  --username deployer \
  --service my_hosting \
  --source "C:\Users\User\Projects\my_app" \
  --destination "/var/www/my_app" \
  --mode CLEAR \
  --action-condition ALWAYS \
  --preview
```

---

## CLI Options Reference

| Argument | Description |
| :--- | :--- |
| `--configfile` | Path to your project-specific Python configuration file |
| `--host` | Remote SSH host |
| `--port` | Remote SSH port (default: `22`) |
| `--username` | Remote SSH username |
| `--service` | Keyring service identifier for password lookup |
| `--source` | Local source folder path |
| `--destination` | Target directory on the remote server |
| `--mode` | Deployment mode (e.g., `UPDATE`, `CLEAR_FORCE`) |
| `--action-condition` | Condition to run post hooks (`NEVER`, `ALWAYS`, `ON_CLEAR`, `ON_UPDATE`) |
| `--action-after` | Remote bash script/command to run upon completion |
| `--action-check` | Remote bash script for verification/polling (fails deploy on error) |
| `--preview` | Boolean flag to enable interactive deployment preview |