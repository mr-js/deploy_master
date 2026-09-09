import argparse
from enum import IntEnum
import getpass
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from fabric import Connection
import keyring
from scp import SCPClient
from tqdm import tqdm


class ClearDestinationMode(IntEnum):
    KEEP_ALL = 0
    SYNC_DELETE = 1
    PURGE_ALL = 2


DEFAULT_IGNORED = {".git", ".idea", "__pycache__", ".venv", "venv", ".env", ".restart-app"}


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


def get_remote_dirs(conn: Connection, remote_dir: str) -> set:
    conn.run(f'mkdir -p "{remote_dir}"', hide=True)
    cmd = f'cd "{remote_dir}" && find . -mindepth 1 -type d'
    res = conn.run(cmd, hide=True, warn=True)
    if not res.ok or not res.stdout.strip():
        return set()

    dirs = set()
    for line in res.stdout.strip().splitlines():
        rel_path = line.replace("./", "", 1).strip()
        if rel_path:
            dirs.add(rel_path)
    return dirs


def get_or_set_password(service: str, username: str) -> str:
    password = keyring.get_password(service, username)
    if not password:
        print(f"Warning: Password not found in keyring for service '{service}' and user '{username}'.")
        choice = input("Would you like to enter and save it now? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            password = getpass.getpass("Enter password: ")
            keyring.set_password(service, username, password)
            print("Password saved to keyring successfully.")
        else:
            password = getpass.getpass("Enter password (session only, not saved): ")
    return password


def load_config_file(config_path: str) -> dict:
    resolved_path = Path(config_path).resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Config file not found: {resolved_path}")

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
    clear_mode: ClearDestinationMode = ClearDestinationMode.SYNC_DELETE,
    restart_file: str = ".restart-app",
    ignored: set = None,
):
    if ignored is None:
        ignored = DEFAULT_IGNORED

    password = get_or_set_password(service, username)

    print(f"Connecting to {username}@{host}:{port}...")
    with Connection(
        host=host,
        user=username,
        port=port,
        connect_kwargs={"password": password},
    ) as c:
        result = c.run("uname -s", hide=True, warn=True)
        if not result.ok:
            raise ConnectionError("SSH connection test failed.")
        print("Connection OK")

        if clear_mode == ClearDestinationMode.PURGE_ALL:
            print("Purging destination directory...")
            c.run(f'find "{destination}" -mindepth 1 -delete', hide=True, warn=True)

        print("Scanning local files...")
        local_map = {}
        local_dirs = set()
        all_local_files = []

        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in ignored]
            for d in dirs:
                dir_full_path = os.path.join(root, d)
                local_dirs.add(os.path.relpath(dir_full_path, source).replace(os.sep, "/"))
            for f in files:
                if f not in ignored:
                    all_local_files.append((root, f))

        for root, file in tqdm(all_local_files, desc="Hashing local files", unit="file"):
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, source).replace(os.sep, "/")
            local_map[rel_path] = (full_path, calculate_local_md5(full_path))

        print("Fetching remote state...")
        remote_map = get_remote_md5_map(c, destination)

        updated_files = []
        unchanged_files = []
        deleted_files = []
        deleted_dirs = []

        for rel_path, (full_path, local_hash) in local_map.items():
            if rel_path not in remote_map or remote_map[rel_path] != local_hash:
                updated_files.append(rel_path)
            else:
                unchanged_files.append(rel_path)

        if clear_mode == ClearDestinationMode.SYNC_DELETE:
            for rel_path in remote_map:
                path_parts = set(rel_path.split("/"))
                if rel_path not in local_map and not (path_parts & ignored):
                    deleted_files.append(rel_path)

            remote_dirs = get_remote_dirs(c, destination)
            for r_dir in remote_dirs:
                dir_parts = set(r_dir.split("/"))
                if r_dir not in local_dirs and not (dir_parts & ignored):
                    deleted_dirs.append(r_dir)
            deleted_dirs.sort(key=lambda x: x.count("/"), reverse=True)

        if updated_files:
            with SCPClient(c.client.get_transport()) as scp:
                for rel_path in tqdm(updated_files, desc="Uploading files", unit="file"):
                    local_full_path, _ = local_map[rel_path]
                    remote_file_path = f"{destination}/{rel_path}"
                    remote_folder = os.path.dirname(remote_file_path).replace(os.sep, "/")
                    c.run(f'mkdir -p "{remote_folder}"', hide=True)
                    scp.put(local_full_path, remote_path=remote_file_path)

        if clear_mode == ClearDestinationMode.SYNC_DELETE:
            if deleted_files:
                for rel_path in tqdm(deleted_files, desc="Deleting old files", unit="file"):
                    c.run(f'rm -f "{destination}/{rel_path}"', hide=True)

            if deleted_dirs:
                for rel_dir in tqdm(deleted_dirs, desc="Deleting old dirs", unit="dir"):
                    c.run(f'rm -rf "{destination}/{rel_dir}"', hide=True)

        print("\n" + "=" * 50)
        print(f"DEPLOYMENT REPORT (Mode: {clear_mode.name} = {clear_mode.value})")
        print("=" * 50)
        print(f"\n[UPDATED / ADDED] ({len(updated_files)}):")
        for f in updated_files:
            print(f"  + {f}")

        if clear_mode == ClearDestinationMode.SYNC_DELETE:
            print(f"\n[DELETED FILES] ({len(deleted_files)}):")
            for f in deleted_files:
                print(f"  - {f}")
            print(f"\n[DELETED DIRECTORIES] ({len(deleted_dirs)}):")
            for d in deleted_dirs:
                print(f"  - [DIR] {d}")
        elif clear_mode == ClearDestinationMode.PURGE_ALL:
            print("\n[DELETED]: All prior destination content wiped completely before sync.")
        else:
            print("\n[DELETED]: None (KEEP_ALL mode).")

        print(f"\n[UNCHANGED] ({len(unchanged_files)}):")
        for f in unchanged_files:
            print(f"    {f}")
        print("=" * 50 + "\n")

        if restart_file:
            print(f"Triggering restart via {restart_file}...")
            c.run(f'touch "{destination}/{restart_file}"')
            print("Restart triggered OK")


def main():
    parser = argparse.ArgumentParser(description="Deploy project via Fabric and SCP with MD5 sync.")
    parser.add_argument("--configfile", help="Path to Python config file (e.g. config.py)")
    parser.add_argument("--host", help="Remote SSH host")
    parser.add_argument("--port", type=int, help="Remote SSH port (default: 22)")
    parser.add_argument("--username", help="Remote SSH username")
    parser.add_argument("--service", help="Keyring service name for password lookup")
    parser.add_argument("--source", help="Local source directory")
    parser.add_argument("--destination", help="Remote destination path")
    parser.add_argument(
        "--mode",
        type=int,
        choices=[0, 1, 2],
        help="0: KEEP_ALL, 1: SYNC_DELETE, 2: PURGE_ALL",
    )
    parser.add_argument(
        "--restart-file",
        help="File to touch for restarting (empty string to disable)",
    )

    args = parser.parse_args()

    file_config = {}
    if args.configfile:
        try:
            file_config = load_config_file(args.configfile)
        except Exception as e:
            print(f"Config Error: {e}", file=sys.stderr)
            sys.exit(1)

    host = args.host or file_config.get("host")
    port = args.port or file_config.get("port") or 22
    username = args.username or file_config.get("username")
    service = args.service or file_config.get("service")
    source = args.source or file_config.get("source")
    destination = args.destination or file_config.get("destination")
    mode_raw = (
        args.mode
        if args.mode is not None
        else file_config.get("clear_mode", ClearDestinationMode.SYNC_DELETE.value)
    )
    restart_file = (
        args.restart_file
        if args.restart_file is not None
        else file_config.get("restart_file", ".restart-app")
    )

    missing = []
    if not host:
        missing.append("--host")
    if not username:
        missing.append("--username")
    if not service:
        missing.append("--service")
    if not source:
        missing.append("--source")
    if not destination:
        missing.append("--destination")

    if missing:
        print(f"Error: Missing required parameters: {', '.join(missing)}", file=sys.stderr)
        print("Provide them via CLI arguments or through --configfile.", file=sys.stderr)
        sys.exit(1)

    try:
        deploy(
            host=host,
            port=int(port),
            username=username,
            service=service,
            source=source,
            destination=destination,
            clear_mode=ClearDestinationMode(int(mode_raw)),
            restart_file=restart_file,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()