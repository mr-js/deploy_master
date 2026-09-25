import argparse
import getpass
import hashlib
import importlib.util
import os
import sys
import time
from enum import Enum
from pathlib import Path

import keyring
import pathspec
from fabric import Connection
from scp import SCPClient
from tqdm import tqdm

RESET = "\033[0m"
RED = "\033[38;5;196m"
GREEN = "\033[38;5;46m"
ORANGE = "\033[38;5;214m"
YELLOW = "\033[38;5;226m"
CYAN = "\033[38;5;51m"
BLUE = "\033[38;5;39m"
GRAY = "\033[38;5;245m"


class Mode(str, Enum):
    UPDATE = "UPDATE"
    UPDATE_FORCE = "UPDATE_FORCE"
    REWRITE = "REWRITE"
    REWRITE_FORCE = "REWRITE_FORCE"
    CLEAR = "CLEAR"
    CLEAR_FORCE = "CLEAR_FORCE"


class ActionCondition(str, Enum):
    NEVER = "NEVER"
    ALWAYS = "ALWAYS"
    ON_CLEAR = "ON_CLEAR"
    ON_UPDATE = "ON_UPDATE"


FALLBACK_IGNORED_LOCAL = [
    "__pycache__/", "*.py[codz]", "*$py.class", "*.so", ".Python", "build/",
    "develop-eggs/", "dist/", "downloads/", "eggs/", ".eggs/", "lib/",
    "lib64/", "parts/", "sdist/", "var/", "wheels/", "share/python-wheels/",
    "*.egg-info/", ".installed.cfg", "*.egg", "MANIFEST", "*.manifest",
    "*.spec", "pip-log.txt", "pip-delete-this-directory.txt", "htmlcov/",
    ".tox/", ".nox/", ".coverage", ".coverage.*", ".cache", "nosetests.xml",
    "coverage.xml", "*.cover", "*.py.cover", "*.lcov", ".hypothesis/",
    ".pytest_cache/", "cover/", "*.mo", "*.pot", "*.log", "local_settings.py",
    "db.sqlite3", "db.sqlite3-journal", "instance/", ".webassets-cache",
    ".scrapy", "docs/_build/", ".pybuilder/", "target/", ".ipynb_checkpoints",
    "profile_default/", "ipython_config.py", ".pdm-python", ".pdm-build/",
    ".pixi/*", "!.pixi/config.toml", "__pypackages__/", "celerybeat-schedule*",
    "celerybeat.pid", "*.rdb", "*.aof", "*.pid", "mnesia/", "rabbitmq/",
    "rabbitmq-data/", "activemq-data/", "*.sage.py", ".env", ".envrc",
    ".venv", "env/", "venv/", "ENV/", "env.bak/", "venv.bak/", ".spyderproject",
    ".spyproject", ".ropeproject", ".idea/", ".vscode/", "/site", ".mypy_cache/",
    ".dmypy.json", "dmypy.json", ".pyre/", ".pytype/", "cython_debug/",
    ".abstra/", "tempCodeRunnerFile.py", ".ruff_cache/", ".pypirc",
    "marimo/_static/", "marimo/_lsp/", "__marimo__/", ".streamlit/secrets.toml",
    ".git/", ".gitignore"
]

FALLBACK_IGNORED_SERVER = [
    ".restart-app"
]


def get_program_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def calculate_local_md5(filepath: str) -> str:
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_remote_md5_map(conn: Connection, remote_dir: str) -> dict:
    conn.run(f'mkdir -p "{remote_dir}"', hide=True)
    cmd = f'cd "{remote_dir}" && find . -type f -exec md5sum {{}} +'
    res = conn.run(cmd, hide=True, warn=True)
    if not res.ok or not res.stdout.strip():
        return {}

    remote_map = {}
    for line in res.stdout.strip().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            checksum, path = parts
            rel_path = path.replace("./", "", 1).strip()
            remote_map[rel_path] = checksum
    return remote_map


def get_or_set_password(service: str, username: str) -> str:
    password = keyring.get_password(service, username)
    if not password:
        print(f"{YELLOW}Warning: Password not found in keyring for '{service}' and user '{username}'.{RESET}")
        choice = input("Would you like to enter and save it now? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            password = getpass.getpass("Enter password: ")
            keyring.set_password(service, username, password)
            print(f"{GREEN}Password saved to keyring successfully.{RESET}")
        else:
            password = getpass.getpass("Enter password (session only, not saved): ")
    return password


def load_config_file(config_path: str) -> dict:
    resolved_path = Path(config_path).resolve()
    if not resolved_path.exists():
        return {}

    spec = importlib.util.spec_from_file_location("dynamic_config", resolved_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load config file: {resolved_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cfg = {}
    for key in dir(module):
        if key.isupper():
            cfg[key.lower()] = getattr(module, key)
    return cfg


def deploy(
    host: str,
    port: int,
    username: str,
    service: str,
    source: str,
    destination: str,
    mode: Mode,
    action_condition: ActionCondition,
    action_after: str,
    action_check: str,
    preview: bool,
    local_ignored: list,
    server_ignored: list,
):
    password = get_or_set_password(service, username)

    local_spec = pathspec.PathSpec.from_lines('gitwildmatch', local_ignored)
    server_spec = pathspec.PathSpec.from_lines('gitwildmatch', server_ignored)

    print(f"\n{GRAY}Connecting to {CYAN}{username}@{host}:{port}{GRAY}...{RESET}")
    with Connection(
        host=host,
        user=username,
        port=port,
        connect_kwargs={"password": password},
    ) as c:
        if not c.run("uname -s", hide=True, warn=True).ok:
            raise ConnectionError("SSH connection test failed.")
        print(f"{GREEN}Connection OK{RESET}")

        c.run(f'mkdir -p "{destination}"', hide=True)

        print(f"{GRAY}Scanning local files...{RESET}")
        local_map = {}
        all_local_files = []

        for root, dirs, files in os.walk(source):
            valid_dirs = []
            for d in dirs:
                dir_rel_path = os.path.relpath(os.path.join(root, d), source).replace(os.sep, "/")
                if not (local_spec.match_file(dir_rel_path) or local_spec.match_file(f"{dir_rel_path}/")):
                    valid_dirs.append(d)
            dirs[:] = valid_dirs

            for f in files:
                file_rel_path = os.path.relpath(os.path.join(root, f), source).replace(os.sep, "/")
                if not local_spec.match_file(file_rel_path):
                    all_local_files.append((root, f))

        for root, file in tqdm(all_local_files, desc="Hashing local files", unit="file", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}"):
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, source).replace(os.sep, "/")
            local_map[rel_path] = (full_path, calculate_local_md5(full_path))

        print(f"{GRAY}Fetching remote state...{RESET}")
        remote_map = get_remote_md5_map(c, destination)

        to_upload = []
        to_delete = []
        skipped_server = []
        unchanged = []

        if mode in (Mode.CLEAR, Mode.CLEAR_FORCE):
            for remote_file in remote_map:
                if mode == Mode.CLEAR and server_spec.match_file(remote_file):
                    skipped_server.append((remote_file, "Protected by SERVER ignore rule (Skip Delete)"))
                else:
                    to_delete.append(remote_file)

        for rel_path, (full_path, local_hash) in local_map.items():
            needs_upload = False
            
            if mode in (Mode.REWRITE, Mode.REWRITE_FORCE, Mode.CLEAR, Mode.CLEAR_FORCE):
                needs_upload = True
            elif rel_path not in remote_map or remote_map[rel_path] != local_hash:
                needs_upload = True
            else:
                unchanged.append(rel_path)

            if needs_upload:
                if mode in (Mode.UPDATE, Mode.REWRITE, Mode.CLEAR) and server_spec.match_file(rel_path):
                    skipped_server.append((rel_path, "Protected by SERVER ignore rule (Skip Upload)"))
                else:
                    to_upload.append(rel_path)

        print(f"\n{YELLOW}=== DEPLOYMENT PLAN (Mode: {mode.value}) ==={RESET}")
        
        if to_upload:
            print(f"\n{GREEN}[TO UPLOAD / UPDATE] ({len(to_upload)}):{RESET}")
            for f in to_upload:
                print(f"  {GREEN}+{RESET} {f}")
        
        if to_delete:
            print(f"\n{RED}[TO DELETE] ({len(to_delete)}):{RESET}")
            for f in to_delete:
                print(f"  {RED}-{RESET} {f}")
                
        if skipped_server:
            print(f"\n{ORANGE}[PROTECTED ON SERVER (SKIPPED)] ({len(skipped_server)}):{RESET}")
            for f, reason in skipped_server:
                print(f"  {ORANGE}*{RESET} {f} {GRAY}- {reason}{RESET}")
                
        if unchanged and not preview:
            print(f"\n{GRAY}[UNCHANGED] ({len(unchanged)}):{RESET}")
            
        print(f"{YELLOW}{'=' * 50}{RESET}\n")

        if preview:
            choice = input(f"{YELLOW}Do you want to proceed with these changes? [y/N]: {RESET}").strip().lower()
            if choice not in ('y', 'yes'):
                print(f"{RED}Deployment aborted by user.{RESET}")
                return

        if to_delete:
            for rel_path in tqdm(to_delete, desc="Deleting old files", unit="file", colour="red"):
                c.run(f'rm -f "{destination}/{rel_path}"', hide=True)
                
            c.run(f'find "{destination}" -mindepth 1 -type d -empty -delete', hide=True, warn=True)

        if to_upload:
            with SCPClient(c.client.get_transport()) as scp:
                for rel_path in tqdm(to_upload, desc="Uploading files", unit="file", colour="green"):
                    local_full_path, _ = local_map[rel_path]
                    remote_file_path = f"{destination}/{rel_path}"
                    remote_folder = os.path.dirname(remote_file_path).replace(os.sep, "/")
                    c.run(f'mkdir -p "{remote_folder}"', hide=True)
                    scp.put(local_full_path, remote_path=remote_file_path)

        should_run_actions = False
        if action_condition == ActionCondition.ALWAYS:
            should_run_actions = True
        elif action_condition == ActionCondition.ON_CLEAR and mode in (Mode.CLEAR, Mode.CLEAR_FORCE):
            should_run_actions = True
        elif action_condition == ActionCondition.ON_UPDATE and (to_upload or to_delete):
            should_run_actions = True

        if should_run_actions and action_after:
            print(f"\n{CYAN}Executing post-deploy action...{RESET}")
            c.run(f'cd "{destination}" && {action_after}')
            print(f"{GREEN}Action command sent.{RESET}")

            if action_check:
                print(f"{GRAY}Executing action check...{RESET}")
                result = c.run(f'cd "{destination}" && {action_check}', warn=True)
                if not result.ok:
                    sys.exit(1)
        elif action_after:
            print(f"\n{GRAY}Skipping post-deploy action (Condition: {action_condition.value} not met).{RESET}")


def main():
    if sys.platform == "win32":
        os.system("color")

    parser = argparse.ArgumentParser(description="Deploy project via Fabric and SCP with MD5 sync.")
    parser.add_argument("--configfile")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username")
    parser.add_argument("--service")
    parser.add_argument("--source")
    parser.add_argument("--destination")
    parser.add_argument("--mode", type=str, choices=[m.value for m in Mode])
    parser.add_argument("--action-condition", type=str, choices=[a.value for a in ActionCondition])
    parser.add_argument("--action-after")
    parser.add_argument("--action-check")
    parser.add_argument("--preview", action="store_true")

    args = parser.parse_args()

    global_config = {}
    global_config_path = os.path.join(get_program_dir(), "config.py")
    if os.path.exists(global_config_path):
        global_config = load_config_file(global_config_path)

    project_config = {}
    if args.configfile:
        project_config = load_config_file(args.configfile)

    def get_val(arg_val, key, default=None):
        if arg_val is not None: return arg_val
        if key in project_config: return project_config[key]
        if key in global_config: return global_config[key]
        return default

    host = get_val(args.host, "host")
    port = get_val(args.port, "port", 22)
    username = get_val(args.username, "username")
    service = get_val(args.service, "service")
    source = get_val(args.source, "source")
    destination = get_val(args.destination, "destination")
    mode_val = get_val(args.mode, "mode", Mode.UPDATE.value)
    action_condition_val = get_val(args.action_condition, "action_condition", ActionCondition.ON_UPDATE.value)
    action_after = get_val(args.action_after, "action_after", "")
    action_check = get_val(args.action_check, "action_check", "")
    preview = args.preview or get_val(False, "preview", False)

    default_ignored_local = global_config.get("default_ignored_local", FALLBACK_IGNORED_LOCAL)
    custom_ignored_local = project_config.get("custom_ignored_local", [])
    merged_local = list(default_ignored_local) + list(custom_ignored_local)

    default_ignored_server = global_config.get("default_ignored_server", FALLBACK_IGNORED_SERVER)
    custom_ignored_server = project_config.get("custom_ignored_server", [])
    merged_server = list(default_ignored_server) + list(custom_ignored_server)

    missing = []
    if not host: missing.append("--host")
    if not username: missing.append("--username")
    if not service: missing.append("--service")
    if not source: missing.append("--source")
    if not destination: missing.append("--destination")

    if missing:
        print(f"{RED}Error: Missing required parameters: {', '.join(missing)}{RESET}", file=sys.stderr)
        sys.exit(1)

    try:
        deploy(
            host=host,
            port=int(port),
            username=username,
            service=service,
            source=source,
            destination=destination,
            mode=Mode(mode_val),
            action_condition=ActionCondition(action_condition_val),
            action_after=action_after,
            action_check=action_check,
            preview=preview,
            local_ignored=merged_local,
            server_ignored=merged_server,
        )
    except Exception as e:
        print(f"{RED}Fatal Error: {e}{RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()