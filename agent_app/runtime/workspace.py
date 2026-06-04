from __future__ import annotations

import subprocess
from typing import Any

from .config import AppConfig


def docker_workspace_status(config: AppConfig) -> dict[str, Any]:
    available = docker_available()
    exists = False
    running = False
    container_id = ""
    image = config.docker_image

    if available:
        inspected = run_docker(["inspect", config.docker_container_name], timeout_seconds=10, check=False)
        exists = inspected["exit_code"] == 0
        if exists:
            data = "Container exists."
            running_result = run_docker(
                ["inspect", "-f", "{{.State.Running}}", config.docker_container_name],
                timeout_seconds=10,
                check=False,
            )
            running = running_result["stdout"].strip().lower() == "true"
            id_result = run_docker(
                ["inspect", "-f", "{{.Id}}", config.docker_container_name],
                timeout_seconds=10,
                check=False,
            )
            image_result = run_docker(
                ["inspect", "-f", "{{.Config.Image}}", config.docker_container_name],
                timeout_seconds=10,
                check=False,
            )
            container_id = id_result["stdout"].strip()[:12]
            image = image_result["stdout"].strip() or image
        else:
            data = inspected["stderr"]
    else:
        data = "Docker is not available on PATH."

    return {
        "ok": available and exists and running,
        "docker_available": available,
        "exists": exists,
        "running": running,
        "container": config.docker_container_name,
        "container_id": container_id,
        "image": image,
        "configured_image": config.docker_image,
        "workdir": config.docker_workdir,
        "workspace": f"docker://{config.docker_container_name}{config.docker_workdir}",
        "details": data,
    }


def reset_docker_workspace(config: AppConfig) -> dict[str, Any]:
    if not docker_available():
        return {"ok": False, "error": "Docker is not available on PATH."}

    steps: list[dict[str, Any]] = []

    inspect_result = run_docker(["inspect", config.docker_container_name], timeout_seconds=15, check=False)
    container_exists = inspect_result["exit_code"] == 0

    if container_exists:
        running_result = run_docker(
            ["inspect", "-f", "{{.State.Running}}", config.docker_container_name],
            timeout_seconds=15,
            check=False,
        )
        if running_result["stdout"].strip().lower() == "true":
            steps.append(named_step("stop", run_docker(["stop", config.docker_container_name], timeout_seconds=60, check=False)))
        steps.append(named_step("remove", run_docker(["rm", config.docker_container_name], timeout_seconds=60, check=False)))

    create_args = [
        "run",
        "-dit",
        "--name",
        config.docker_container_name,
        "-w",
        config.docker_workdir,
        config.docker_image,
        "bash",
    ]
    steps.append(named_step("create", run_docker(create_args, timeout_seconds=120, check=False)))
    steps.append(named_step("prepare-workdir", run_docker(["exec", config.docker_container_name, "mkdir", "-p", config.docker_workdir], timeout_seconds=30, check=False)))

    failed = [step for step in steps if step["exit_code"] != 0]
    status = docker_workspace_status(config)
    return {
        "ok": not failed and status["running"],
        "steps": steps,
        "status": status,
        "error": failed[0]["stderr"] if failed else "",
    }


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def run_docker(args: list[str], timeout_seconds: int, check: bool) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Timed out after {timeout_seconds} seconds.",
        }
    except OSError as exc:
        return {"exit_code": 127, "stdout": "", "stderr": str(exc)}

    if check and result.returncode != 0:
        return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def named_step(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"].strip(),
        "stderr": result["stderr"].strip(),
    }

