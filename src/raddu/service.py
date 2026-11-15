"""Running the adhan / salawat watcher in the background.

Two ways, both for the stand-alone modes only (never for recitations):

* ``-b`` / ``--background`` detaches the current command from the
  terminal. Its pid is kept in the data dir so ``--status`` and ``--stop``
  can find it, and what it does is logged to ``raddu.log`` beside it.
* ``--autostart`` installs a systemd user service, so the watcher starts
  by itself at every login and is restarted if it ever exits.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

from . import config

UNIT = "raddu.service"


class ServiceError(Exception):
    """Background start/stop or autostart failed."""


def pid_path() -> pathlib.Path:
    return config.data_dir() / "raddu.pid"


def log_path() -> pathlib.Path:
    return config.data_dir() / "raddu.log"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running() -> tuple[int, str] | None:
    """(pid, command) of the background watcher, or None."""
    p = pid_path()
    try:
        pid_s, _, cmd = p.read_text(encoding="utf-8").partition("\n")
        pid = int(pid_s)
    except (OSError, ValueError):
        return None
    if not _alive(pid):
        p.unlink(missing_ok=True)
        return None
    return pid, cmd.strip()


def _command(args: list[str]) -> list[str]:
    """argv that re-runs raddu with ``args``, from the same source tree."""
    return [sys.executable, "-m", "raddu", *args]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(pathlib.Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def start(args: list[str]) -> int:
    """Start ``raddu <args>`` detached; return its pid."""
    current = running()
    if current:
        raise ServiceError(
            f"already running in the background (pid {current[0]}: raddu {current[1]}); "
            "stop it first with 'raddu --stop'"
        )
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        proc = subprocess.Popen(
            _command(args), env=_env(), stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
        )
    pid_path().write_text(f"{proc.pid}\n{' '.join(args)}\n", encoding="utf-8")
    time.sleep(0.5)
    if proc.poll() is not None:
        pid_path().unlink(missing_ok=True)
        raise ServiceError(f"the background watcher exited at once; see {log}")
    return proc.pid


def stop() -> int | None:
    """Stop the background watcher; return its pid, or None if none ran."""
    current = running()
    if not current:
        return None
    pid = current[0]
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
    pid_path().unlink(missing_ok=True)
    return pid


# -- autostart (systemd --user) ---------------------------------------------
def unit_path() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config"
    return pathlib.Path(base) / "systemd" / "user" / UNIT


def _systemctl(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *argv],
                          capture_output=True, text=True)


def _require_systemd() -> None:
    if not shutil.which("systemctl"):
        raise ServiceError(
            "autostart needs systemd, which was not found; add "
            "'raddu --adhan -b' to your login script instead"
        )


def _quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' if (
        not arg or any(c in arg for c in ' "\\\'$%')) else arg


def enable_autostart(args: list[str]) -> pathlib.Path:
    _require_systemd()
    src = pathlib.Path(__file__).resolve().parent.parent
    unit = unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\n"
        "Description=raddu - adhan at prayer times and salawat reminder\n"
        "After=sound.target pipewire.service pulseaudio.service\n"
        "\n"
        "[Service]\n"
        f"Environment=PYTHONPATH={src}\n"
        f"ExecStart={' '.join(_quote(a) for a in _command(args))}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    for step in (("daemon-reload",), ("enable", UNIT), ("restart", UNIT)):
        res = _systemctl(*step)
        if res.returncode != 0:
            raise ServiceError(f"systemctl --user {' '.join(step)} failed: "
                               f"{(res.stderr or res.stdout).strip()}")
    return unit


def disable_autostart() -> bool:
    unit = unit_path()
    if not unit.exists():
        return False
    _require_systemd()
    _systemctl("disable", "--now", UNIT)
    unit.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    return True


def autostart_state() -> str | None:
    """'active', 'inactive', ... if the unit is installed, else None."""
    if not unit_path().exists():
        return None
    if not shutil.which("systemctl"):
        return "installed"
    return _systemctl("is-active", UNIT).stdout.strip() or "unknown"


def autostart_command() -> str | None:
    unit = unit_path()
    try:
        for line in unit.read_text(encoding="utf-8").splitlines():
            if line.startswith("ExecStart="):
                return line.split(" -m raddu ", 1)[-1]
    except OSError:
        pass
    return None
