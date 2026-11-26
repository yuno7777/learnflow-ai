"""Bootstrap the LearnFlow Compose stack from PowerShell, bash, or zsh."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = {"JWT_SECRET", "WEBHOOK_SIGNING_SECRET", "N8N_ENCRYPTION_KEY"}


def init_env(root: Path) -> bool:
    """Create a local configuration with unique secrets and preserve existing files."""
    destination = root / ".env"
    if destination.exists():
        return False

    template = (root / ".env.example").read_text(encoding="utf-8")
    lines = []
    for line in template.splitlines():
        key, _, _ = line.partition("=")
        if key in SECRET_NAMES:
            line = f"{key}={secrets.token_hex(32)}"
        lines.append(line)

    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "doctor", "up"))
    command = parser.parse_args().command

    try:
        if command in {"init", "up"}:
            created = init_env(ROOT)
            print("Created .env with local secrets." if created else "Using the existing .env.", flush=True)
            if command == "init":
                return 0

        if not (ROOT / ".env").is_file():
            parser.exit(1, "Run the init command to create .env first.\n")

        docker = shutil.which("docker")
        if docker is None:
            parser.exit(1, "Install Docker with the Compose v2 plugin and put docker on PATH.\n")

        compose = [docker, "compose", "--env-file", ".env"]
        if command == "doctor":
            subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], cwd=ROOT, check=True)
            subprocess.run(compose + ["config", "--quiet"], cwd=ROOT, check=True)
            print("Docker is reachable and the Compose configuration is valid.")
        else:
            subprocess.run(
                compose + ["up", "--build", "--wait", "--wait-timeout", "300"],
                cwd=ROOT,
                check=True,
            )
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    except OSError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
