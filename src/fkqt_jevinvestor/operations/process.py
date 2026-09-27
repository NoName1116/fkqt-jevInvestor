import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile

LOG_LIMIT = 65536


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    safe_stdout: str
    safe_stderr: str
    timed_out: bool
    duration_seconds: float


def redact(text: str, env: Mapping[str, str]) -> str:
    for name, value in env.items():
        if value and any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)[\s\"']*[:=][\s\"']*)[^\s,\"'&]+", r"\1[REDACTED]", text)
    return text.encode("utf-8")[:LOG_LIMIT].decode("utf-8", errors="ignore")


def run_cli(argv: tuple[str, ...], cwd: Path, *, timeout_seconds: int, env: Mapping[str, str]) -> ProcessOutcome:
    if not argv or not Path(argv[0]).is_absolute() or not cwd.is_absolute() or timeout_seconds <= 0:
        raise ValueError("FORWARD_PROCESS_ARGUMENT_INVALID")
    started = time.monotonic()
    timed_out = False
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=dict(env), shell=False,
                                       stdout=stdout, stderr=stderr,
                                       start_new_session=os.name != "nt",
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            return ProcessOutcome(2, "", "FORWARD_PROCESS_START_FAILED", False, time.monotonic() - started)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(("taskkill", "/PID", str(process.pid), "/T", "/F"),
                               capture_output=True, timeout=10, check=False,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        stdout.seek(0)
        stderr.seek(0)
        margin = max((len(value.encode("utf-8")) for name, value in env.items()
                      if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH"))), default=0)
        output = redact(stdout.read(LOG_LIMIT + margin).decode("utf-8", errors="replace"), env)
        error = redact(stderr.read(LOG_LIMIT + margin).decode("utf-8", errors="replace"), env)
        return ProcessOutcome(process.returncode or (2 if timed_out else 0), output, error, timed_out, time.monotonic() - started)
