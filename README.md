# deploy_master
Deploy your Python project to shared hosting quickly and easily

## Description

Lightweight deployment tool for Python applications on shared hosting (ISPmanager, cPanel) via SSH/SCP with MD5 sync and app restart.

## Features

- **MD5 Hash Sync**: Compares local and remote files to transfer only modified or added items.
- **Remote Cleanup Modes**: Configurable strategies for managing obsolete destination files.
- **Secure Password Storage**: Uses OS keyring (`keyring`) with interactive prompt fallback.
- **Visual Progress**: Real-time progress bars via `tqdm` for hashing, uploading, and deletion.
- **Application Reload**: Touches a reload marker file (e.g., `.restart-app`) upon completion.
- **Flexible Configuration**: Supports CLI flags, Python configuration files, and direct programmatic usage.

---

## Installation

This project is built and managed using [`uv`](https://github.com/astral-sh/uv).

Clone the repository and install dependencies:

```bash
git clone [https://github.com/mr-js.ru/deploy_master.git](https://github.com/your-username/deploy_master.git)
cd deploy_master
uv sync
```

---

## Configuration

You can provide deployment parameters via command-line arguments or using a configuration file.

### `config.py`

Create a configuration file in the project root:

```python
HOST = "192.0.2.1"
PORT = 22
USERNAME = "deployer"
SERVICE = "my_hosting"
SOURCE = r"C:\Users\User\Projects\my_app"
DESTINATION = r"/var/www/my_app"
CLEAR_MODE = 1
RESTART_FILE = ".restart-app"
```

### Cleanup Modes (`--mode`)

| Mode | Name | Description |
| :--- | :--- | :--- |
| `0` | `KEEP_ALL` | Files are uploaded and overwritten; no remote files are deleted. |
| `1` | `SYNC_DELETE` | Default. Removes obsolete remote files and directories not present locally. |
| `2` | `PURGE_ALL` | Completely wipes remote destination contents before transferring. |

---

## Usage Methods

### 1. Using `uv run` (Recommended)

Run directly through `uv` without manually managing virtual environment states:

```bash
# Using a configuration file
uv run deploy-master --configfile config.py

# Overriding specific parameters via CLI flags
uv run deploy-master --configfile config.py --mode 0

# Using explicit CLI flags only
uv run deploy-master \
  --host 192.0.2.1 \
  --port 22 \
  --username deployer \
  --service my_hosting \
  --source "C:\Users\User\Projects\my_app" \
  --destination "/var/www/my_app" \
  --mode 1

# Running as a Python module via uv
uv run python -m deploy_master.main --configfile config.py
```

---

### 2. From an Activated Virtual Environment

If your virtual environment (`.venv`) is already active:

```bash
# Direct CLI entrypoint
deploy-master --configfile config.py

# Standard module execution
python -m deploy_master.main --configfile config.py
```

---

### 3. Direct Execution (CI/CD, Batch, Task Scheduler)

Call the virtual environment binaries directly without activation:

**Windows (cmd / PowerShell):**
```powershell
.\.venv\Scripts\deploy-master.exe --configfile config.py
# or
.\.venv\Scripts\python.exe -m deploy_master.main --configfile config.py
```

**Linux / macOS:**
```bash
./.venv/bin/deploy-master --configfile config.py
# or
./.venv/bin/python -m deploy_master.main --configfile config.py
```

---

### 4. Global System Tool (`uv tool`)

Install the script globally across your operating system:

```bash
# From within the repository folder
uv tool install .
```

Run from any directory:

```bash
deploy-master --configfile /path/to/config.py
```

---

### 5. Programmatic Python Import

Import `deploy` and `ClearDestinationMode` into other automation workflows:

```python
from deploy_master.main import ClearDestinationMode, deploy

deploy(
    host="192.0.2.1",
    port=22,
    username="deployer",
    service="my_hosting",
    source=r"C:\Users\User\Projects\my_app",
    destination=r"/var/www/my_app",
    clear_mode=ClearDestinationMode.SYNC_DELETE,
    restart_file=".restart-app",
)
```

---

## CLI Options Reference

| Argument | Type | Description |
| :--- | :--- | :--- |
| `--configfile` | `string` | Path to Python configuration file (e.g., `config.py`) |
| `--host` | `string` | Remote SSH host |
| `--port` | `int` | Remote SSH port (default: `22`) |
| `--username` | `string` | Remote SSH username |
| `--service` | `string` | Keyring service identifier for password lookup |
| `--source` | `string` | Local source folder path |
| `--destination` | `string` | Target directory on the remote server |
| `--mode` | `0 \| 1 \| 2` | Cleanup mode (`0`: `KEEP_ALL`, `1`: `SYNC_DELETE`, `2`: `PURGE_ALL`) |
| `--restart-file` | `string` | Remote file marker touched upon completion (default: `.restart-app`) |