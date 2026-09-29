#!/usr/bin/env python3
"""V18 content-grounded foreground orchestrator with watchdog-only recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "semantic-production-orchestrator-state/v260928"
CONFIG_SCHEMA = "semantic-production-pipeline-config/v260928"
MODEL_RESPONSE_SCHEMA = "semantic-model-phase-response/v260928"
LEASE_SCHEMA = "semantic-foreground-lease/v260928"
FRONT_STATUS_SCHEMA = "semantic-front-status/v260928"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"object required: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def save_state(path: Path, state: dict, backup: bool = True) -> None:
    if backup and path.is_file():
        backup_dir = path.parent / ".v18" / "state_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, backup_dir / f"{path.name}.bak-{stamp}")
    state["updated_at"] = now_iso()
    state["revision"] = int(state.get("revision", 0)) + 1
    atomic_json(path, state)


def validate_config(config: dict) -> None:
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("pipeline config schema mismatch")
    if not isinstance(config.get("task_id"), str) or not config["task_id"]:
        raise ValueError("task_id required")
    if not isinstance(config.get("task_root"), str):
        raise ValueError("task_root required")
    phases = config.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("non-empty phases required")
    names = []
    for phase in phases:
        if not isinstance(phase, dict) or phase.get("kind") not in {"local", "chunked_local", "model"} or not phase.get("name"):
            raise ValueError("each phase needs name and local/chunked_local/model kind")
        if phase["kind"] in {"local", "chunked_local"} and not isinstance(phase.get("command"), list):
            raise ValueError(f"local phase command required: {phase.get('name')}")
        names.append(phase["name"])
    if len(names) != len(set(names)):
        raise ValueError("phase names must be unique")
    if "CANDIDATE_BUILD" in names:
        required_order = ("SOURCE_ASR", "CANDIDATE_PROPOSAL", "SOURCE_FRAME_EVIDENCE", "CANDIDATE_BUILD")
        missing = [name for name in required_order if name not in names]
        if missing:
            raise ValueError("V14_SOURCE_FRAME_PIPELINE_MISSING:" + ",".join(missing))
        positions = [names.index(name) for name in required_order]
        if positions != sorted(positions):
            raise ValueError("V14_SOURCE_FRAME_PIPELINE_ORDER_INVALID")
        if not isinstance(config.get("variables", {}).get("source_frame_evidence_index"), str):
            raise ValueError("V14_SOURCE_FRAME_INDEX_VARIABLE_REQUIRED")
    if config.get("mode", "production") == "production":
        if config.get("heartbeat", {}).get("required") is not True:
            raise ValueError("production requires heartbeat")
        if not isinstance(config.get("checkpoint_command"), list) or not config["checkpoint_command"]:
            raise ValueError("production requires checkpoint_command")
        execution = config.get("execution")
        if not isinstance(execution, dict) or execution.get("foreground_required") is not True or execution.get("heartbeat_recovery_only") is not True:
            raise ValueError("V14_REQUIRES_FOREGROUND_AND_WATCHDOG_ONLY_HEARTBEAT")
        if float(execution.get("heartbeat_stale_after_seconds", 0)) < 60:
            raise ValueError("heartbeat_stale_after_seconds must be at least 60")
        if float(execution.get("recovery_budget_seconds", 0)) < 30:
            raise ValueError("recovery_budget_seconds must be at least 30")
        job = config.get("job_contract")
        if not isinstance(job, dict):
            raise ValueError("V14_JOB_CONTRACT_REQUIRED")
        required_job = ["requested_outputs", "style_profile_id", "content_grounding_contract_id", "inventory_mode", "source_scope_mode", "source_scope_total", "source_scope_justification"]
        missing_job = [key for key in required_job if job.get(key) in (None, "")]
        if missing_job:
            raise ValueError("V14_JOB_CONTRACT_MISSING:" + ",".join(missing_job))
        if int(job["requested_outputs"]) < 1 or int(job["source_scope_total"]) < 1:
            raise ValueError("V14_JOB_CONTRACT_COUNTS_INVALID")
        if job["inventory_mode"] not in {"witness_sufficient", "exhaustive"}:
            raise ValueError("V14_INVENTORY_MODE_INVALID")
        if job["source_scope_mode"] not in {"explicit_witness_manifest", "explicit_exhaustive_manifest"}:
            raise ValueError("V14_SOURCE_SCOPE_MODE_INVALID")
        variables = config.get("variables", {})
        profile_path = Path(str(variables.get("style_profile", "")))
        content_contract_path = Path(str(variables.get("content_grounding_contract", "")))
        manifest_path = Path(str(variables.get("source_manifest", "")))
        if not profile_path.is_file():
            raise ValueError("V14_STYLE_PROFILE_MISSING")
        profile = load_json(profile_path)
        if profile.get("profile") != job["style_profile_id"]:
            raise ValueError("V14_STYLE_PROFILE_BINDING_MISMATCH")
        if not content_contract_path.is_file():
            raise ValueError("V18_CONTENT_GROUNDING_CONTRACT_MISSING")
        content_contract = load_json(content_contract_path)
        if content_contract.get("schema") not in {"celebrity-product-content-grounding-contract/v260928", "celebrity-product-content-grounding-contract/v260928"} or content_contract.get("contract_id") != job["content_grounding_contract_id"]:
            raise ValueError("V19_CONTENT_GROUNDING_CONTRACT_BINDING_MISMATCH")
        profile_policy = profile.get("policy", {}) if isinstance(profile.get("policy"), dict) else {}
        if profile_policy.get("inventory_mode") != job["inventory_mode"]:
            raise ValueError("V14_PROFILE_INVENTORY_MODE_MISMATCH")
        expected_scope_mode = "explicit_witness_manifest" if job["inventory_mode"] == "witness_sufficient" else "explicit_exhaustive_manifest"
        if job["source_scope_mode"] != expected_scope_mode:
            raise ValueError("V14_SOURCE_SCOPE_MODE_INVENTORY_MISMATCH")
        if not manifest_path.is_file():
            raise ValueError("V14_SOURCE_SCOPE_MANIFEST_MISSING")
        manifest = load_json(manifest_path)
        sources = manifest.get("sources")
        if not isinstance(sources, list) or len(sources) != int(job["source_scope_total"]) or int(manifest.get("source_count", -1)) != len(sources):
            raise ValueError("V14_SOURCE_SCOPE_COUNT_MISMATCH")
        if str(variables.get("source_scope_total")) != str(job["source_scope_total"]):
            raise ValueError("V14_SOURCE_SCOPE_VARIABLE_MISMATCH")
        if str(variables.get("requested_outputs")) != str(job["requested_outputs"]):
            raise ValueError("V14_REQUESTED_OUTPUTS_VARIABLE_MISMATCH")


def verify_heartbeat_registration(config: dict) -> None:
    heartbeat = config.get("heartbeat", {})
    if not isinstance(heartbeat, dict) or heartbeat.get("required") is not True:
        return
    raw_path = str(heartbeat.get("registration_path", "")).replace("${task_root}", str(Path(config["task_root"]).resolve())).replace("${package_root}", str(Path(__file__).resolve().parent.parent))
    path = Path(raw_path)
    expected_hash = heartbeat.get("registration_sha256")
    ref = verify_ref(str(path), expected_hash)
    registration = load_json(Path(ref["path"]))
    if registration.get("schema") != "semantic-heartbeat-registration/v260928" or registration.get("task_id") != config.get("task_id") or registration.get("status") != "active" or registration.get("role") != "watchdog_only" or not registration.get("automation_id"):
        raise ValueError("HEARTBEAT_REGISTRATION_INVALID")


def expand(value: str, config: dict, state_path: Path) -> str:
    package_root = Path(__file__).resolve().parent.parent
    tokens = {
        "${task_root}": str(Path(config["task_root"]).resolve()),
        "${package_root}": str(package_root),
        "${python}": sys.executable,
        "${state_path}": str(state_path.resolve()),
    }
    repair_round = 0
    if state_path.is_file():
        try:
            repair_round = int(load_json(state_path).get("repair_round", 0))
        except Exception:
            repair_round = 0
    tokens["${repair_round}"] = str(repair_round)
    for key, replacement in tokens.items():
        value = value.replace(key, replacement)
    for key, replacement in config.get("variables", {}).items():
        value = value.replace("${" + str(key) + "}", str(replacement))
    return value


def expand_command(command: list, config: dict, state_path: Path) -> list[str]:
    return [expand(str(item), config, state_path) for item in command]


def verify_ref(path_value: str, hash_value: str | None = None) -> dict:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha_file(path)
    if hash_value and actual.lower() != hash_value.lower():
        raise ValueError(f"hash mismatch: {path}")
    return {"path": str(path), "sha256": actual}


def collect_outputs(phase: dict, config: dict, state_path: Path) -> dict:
    outputs = {}
    for item in phase.get("outputs", []):
        name = item.get("name")
        path = expand(str(item.get("path", "")), config, state_path)
        if not name:
            raise ValueError(f"unnamed output in phase {phase['name']}")
        outputs[name] = verify_ref(path)
    return outputs


def progress_snapshot(phase: dict, config: dict, state_path: Path) -> dict | None:
    spec = phase.get("progress")
    if not isinstance(spec, dict):
        return None
    directory = Path(expand(str(spec.get("directory", "")), config, state_path))
    pattern = str(spec.get("glob", "*"))
    count = len(list(directory.glob(pattern))) if directory.is_dir() else 0
    raw_total = spec.get("total")
    expanded_total = expand(str(raw_total), config, state_path) if raw_total is not None else None
    total = int(expanded_total) if isinstance(expanded_total, str) and expanded_total.isdigit() else expanded_total
    return {"completed": count, "total": total, "directory": str(directory), "glob": pattern}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return False
    if process_id <= 0:
        return False
    if os.name == "nt":
        # On Windows, os.kill(pid, 0) calls TerminateProcess rather than
        # providing POSIX-style liveness probing. Query a process handle instead.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x101000, False, process_id)  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied proves that a protected process with this PID exists.
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def emit_checkpoint(config: dict, state_path: Path, state: dict, summary: str) -> None:
    template = config.get("checkpoint_command")
    if not isinstance(template, list) or not template:
        return
    command = [
        expand(str(item), config, state_path)
        .replace("${phase}", str(state.get("current_phase")))
        .replace("${summary}", summary)
        .replace("${task_id}", str(state.get("task_id")))
        for item in template
    ]
    result = subprocess.run(command, cwd=config["task_root"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        state.setdefault("warnings", []).append({"at": now_iso(), "code": "CHECKPOINT_COMMAND_FAILED", "detail": result.stderr[-1000:]})


def phase_result_path(config: dict, phase_index: int, attempt: int) -> Path:
    phase = config["phases"][phase_index]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in phase["name"])
    return Path(config["task_root"]) / ".v8" / "phase_results" / f"{phase_index:02d}-{safe}-attempt{attempt}.json"


def phase_command(phase: dict, attempt: int) -> list:
    if attempt == 0:
        return phase["command"]
    fallbacks = phase.get("fallback_commands", [])
    index = attempt - 1
    if index >= len(fallbacks):
        raise IndexError("no fallback command")
    return fallbacks[index]


def launch_worker(config: dict, state_path: Path, state: dict) -> None:
    index = int(state["phase_index"])
    phase = config["phases"][index]
    attempt = int(state.get("phase_attempt", 0))
    command = expand_command(phase_command(phase, attempt), config, state_path)
    result_path = phase_result_path(config, index, attempt)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = Path(config["task_root"]) / ".v8" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{index:02d}-{phase['name']}-attempt{attempt}.stdout.log"
    stderr_path = log_dir / f"{index:02d}-{phase['name']}-attempt{attempt}.stderr.log"
    worker = [sys.executable, str(Path(__file__).resolve()), "_worker", "--command-json", json.dumps(command, ensure_ascii=False), "--cwd", str(Path(config["task_root"]).resolve()), "--result", str(result_path), "--stdout", str(stdout_path), "--stderr", str(stderr_path)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(worker, creationflags=flags, close_fds=True)
    worker_pid = process.pid
    if os.name == "nt":
        process._handle.Close()
        process.returncode = 0
    state["status"] = "BACKGROUND_RUNNING"
    state["active_worker"] = {"pid": worker_pid, "phase": phase["name"], "attempt": attempt, "result_path": str(result_path), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "started_at": now_iso(), "command": command}
    state["last_progress"] = progress_snapshot(phase, config, state_path)
    save_state(state_path, state)
    emit_checkpoint(config, state_path, state, f"launched local phase {phase['name']} attempt {attempt}")


def complete_phase(config: dict, state_path: Path, state: dict, outputs: dict, detail: str) -> None:
    phase = config["phases"][int(state["phase_index"])]
    state.setdefault("artifacts", {}).update(outputs)
    state.setdefault("history", []).append({"phase": phase["name"], "completed_at": now_iso(), "attempt": int(state.get("phase_attempt", 0)), "detail": detail, "outputs": outputs})
    state["phase_index"] = int(state["phase_index"]) + 1
    state["phase_attempt"] = 0
    state["chunk_run"] = 0
    state["active_worker"] = None
    state["pending_model_action"] = None
    if state["phase_index"] >= len(config["phases"]):
        state["status"] = "COMPLETE"
        state["current_phase"] = "COMPLETE"
    else:
        state["status"] = "READY"
        state["current_phase"] = config["phases"][state["phase_index"]]["name"]
    save_state(state_path, state)
    emit_checkpoint(config, state_path, state, f"completed phase {phase['name']}")


def fail_or_retry(config: dict, state_path: Path, state: dict, detail: str) -> None:
    phase = config["phases"][int(state["phase_index"])]
    attempt = int(state.get("phase_attempt", 0))
    max_retries = int(phase.get("max_retries", config.get("default_max_retries", 1)))
    fallback_count = len(phase.get("fallback_commands", []))
    state.setdefault("failures", []).append({"phase": phase["name"], "attempt": attempt, "at": now_iso(), "detail": detail})
    if attempt < max_retries and attempt < fallback_count:
        state["phase_attempt"] = attempt + 1
        state["chunk_run"] = 0
        state["active_worker"] = None
        state["status"] = "READY"
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"retrying phase {phase['name']} after: {detail[:300]}")
    else:
        state["status"] = "FAILED"
        state["active_worker"] = None
        state["failure"] = {"phase": phase["name"], "detail": detail, "at": now_iso()}
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"phase failed {phase['name']}: {detail[:300]}")


def init_orchestrator(config_path: Path, state_path: Path) -> dict:
    if state_path.exists():
        raise FileExistsError(state_path)
    config = load_json(config_path)
    validate_config(config)
    verify_heartbeat_registration(config)
    first = config["phases"][0]["name"]
    state = {"schema": SCHEMA, "task_id": config["task_id"], "config_path": str(config_path.resolve()), "config_sha256": sha_file(config_path), "phase_index": 0, "current_phase": first, "phase_attempt": 0, "chunk_run": 0, "repair_round": 0, "status": "READY", "revision": 0, "updated_at": now_iso(), "active_worker": None, "pending_model_action": None, "artifacts": {}, "history": [], "failures": [], "warnings": []}
    save_state(state_path, state, backup=False)
    write_front_status(config, state, None, None, "ready")
    emit_checkpoint(config, state_path, state, "orchestrator initialized")
    return state


def load_bound(config_path: Path, state_path: Path) -> tuple[dict, dict]:
    config = load_json(config_path)
    validate_config(config)
    verify_heartbeat_registration(config)
    state = load_json(state_path)
    if state.get("schema") != SCHEMA or state.get("task_id") != config.get("task_id"):
        raise ValueError("state/config identity mismatch")
    if state.get("config_sha256") != sha_file(config_path):
        raise ValueError("CONFIG_HASH_MISMATCH")
    for name, ref in state.get("artifacts", {}).items():
        verify_ref(ref["path"], ref["sha256"])
    return config, state


def progress_count(snapshot: dict | None) -> int:
    return int(snapshot.get("completed", 0)) if isinstance(snapshot, dict) else 0


def run_local_heartbeat_slice(config: dict, state_path: Path, state: dict, phase: dict) -> tuple[int, dict]:
    attempt = int(state.get("phase_attempt", 0))
    chunk = int(state.get("chunk_run", 0))
    command = expand_command(phase_command(phase, attempt), config, state_path)
    log_dir = Path(config["task_root"]) / ".v18" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{state['phase_index']:02d}-{phase['name']}-attempt{attempt}-chunk{chunk}.stdout.log"
    stderr_path = log_dir / f"{state['phase_index']:02d}-{phase['name']}-attempt{attempt}-chunk{chunk}.stderr.log"
    before = progress_snapshot(phase, config, state_path)
    timed_out = False
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        try:
            result = subprocess.run(command, cwd=config["task_root"], stdout=out, stderr=err, shell=False, timeout=float(phase.get("slice_timeout_seconds", config.get("slice_timeout_seconds", 240))))
            code = int(result.returncode)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = 124
    after = progress_snapshot(phase, config, state_path)
    gained = progress_count(after) > progress_count(before)
    state["last_progress"] = after
    state["active_worker"] = None
    state["chunk_run"] = chunk + 1
    if code == 0:
        try:
            outputs = collect_outputs(phase, config, state_path)
        except Exception as exc:
            fail_or_retry(config, state_path, state, f"output verification failed: {type(exc).__name__}: {exc}")
            return 2, load_json(state_path)
        complete_phase(config, state_path, state, outputs, "bounded local slice completed and outputs verified")
        updated = load_json(state_path)
        return (0 if updated["status"] == "COMPLETE" else 22), updated
    if phase["kind"] == "chunked_local" and (code == int(phase.get("continue_exit_code", 75)) or (gained and phase.get("recover_progress_on_failure", True))):
        state["status"] = "CHUNK_PENDING"
        state.setdefault("history", []).append({"phase": phase["name"], "at": now_iso(), "detail": "chunk_progress", "returncode": code, "timed_out": timed_out, "progress": after})
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"chunk {chunk} completed/progressed for {phase['name']}")
        return 22, state
    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    fail_or_retry(config, state_path, state, f"bounded local exit={code} timeout={timed_out} stderr={stderr_tail}")
    updated = load_json(state_path)
    return (22 if updated["status"] == "READY" else 2), updated


def resume(config_path: Path, state_path: Path) -> tuple[int, dict]:
    config, state = load_bound(config_path, state_path)
    if state["status"] == "COMPLETE":
        return 0, state
    if state["status"] == "FAILED":
        return 2, state
    if state["status"] == "WAITING_MODEL":
        return 20, state
    if state["status"] == "CHUNK_PENDING":
        state["status"] = "READY"
        save_state(state_path, state)
    if int(state["phase_index"]) >= len(config["phases"]):
        state["status"] = "COMPLETE"
        save_state(state_path, state)
        return 0, state
    phase = config["phases"][int(state["phase_index"])]
    state["current_phase"] = phase["name"]
    if phase["kind"] == "model":
        request = {"schema": "semantic-model-phase-request/v260928", "task_id": state["task_id"], "phase": phase["name"], "created_at": now_iso(), "instructions": expand(str(phase.get("instructions", "")), config, state_path), "model_role": phase.get("model_role", "gpt-5.6-terra"), "inputs": state.get("artifacts", {}), "required_outputs": phase.get("outputs", []), "response_path": expand(str(phase["response_path"]), config, state_path)}
        pending_path = Path(config["task_root"]) / ".v18" / "pending_model_action.json"
        atomic_json(pending_path, request)
        state["status"] = "WAITING_MODEL"
        state["pending_model_action"] = {"path": str(pending_path.resolve()), "sha256": sha_file(pending_path), "phase": phase["name"], "response_path": request["response_path"]}
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"waiting for Codex model phase {phase['name']}")
        return 20, state
    return run_local_heartbeat_slice(config, state_path, state, phase)


def supply_model(config_path: Path, state_path: Path, response_path: Path) -> tuple[int, dict]:
    config, state = load_bound(config_path, state_path)
    if state.get("status") != "WAITING_MODEL":
        raise ValueError("not waiting for model phase")
    response = load_json(response_path)
    phase = config["phases"][int(state["phase_index"])]
    if response.get("schema") != MODEL_RESPONSE_SCHEMA or response.get("task_id") != state["task_id"] or response.get("phase") != phase["name"]:
        raise ValueError("model response binding mismatch")
    if response.get("decision") == "repair_required":
        outputs = {}
        expected_paths = {item["name"]: Path(expand(str(item["path"]), config, state_path)).resolve() for item in phase.get("outputs", [])}
        for item in response.get("outputs", []):
            if item.get("name") not in expected_paths or Path(item["path"]).resolve() != expected_paths[item["name"]]:
                raise ValueError(f"model repair output path mismatch: {item.get('name')}")
            outputs[item["name"]] = verify_ref(item["path"], item["sha256"])
        required = {item["name"] for item in phase.get("outputs", [])}
        if required - set(outputs):
            raise ValueError("model repair response missing outputs: " + ",".join(sorted(required - set(outputs))))
        repair = phase.get("on_repair", {})
        allowed = repair.get("allowed_phases", [])
        target = response.get("repair_phase") or repair.get("default_phase")
        if target not in allowed:
            raise ValueError(f"repair phase not allowed: {target}")
        new_round = int(state.get("repair_round", 0)) + 1
        if new_round > int(repair.get("max_rounds", 0)):
            fail_or_retry(config, state_path, state, "repair round limit exhausted")
            return 2, load_json(state_path)
        target_index = next((index for index, item in enumerate(config["phases"]) if item["name"] == target), None)
        if target_index is None or target_index >= int(state["phase_index"]):
            raise ValueError("repair target must be an earlier phase")
        invalid_names = {
            output["name"]
            for later in config["phases"][target_index:]
            for output in later.get("outputs", [])
        }
        artifacts = state.setdefault("artifacts", {})
        for name in invalid_names:
            artifacts.pop(name, None)
        artifacts.update(outputs)
        state.setdefault("history", []).append({"phase": phase["name"], "completed_at": now_iso(), "detail": "repair_required", "repair_round": new_round, "outputs": outputs})
        state["repair_round"] = new_round
        state["phase_index"] = target_index
        state["current_phase"] = target
        state["phase_attempt"] = 0
        state["chunk_run"] = 0
        state["status"] = "READY"
        state["active_worker"] = None
        state["pending_model_action"] = None
        save_state(state_path, state)
        emit_checkpoint(config, state_path, state, f"repair round {new_round} rewound to {target}")
        return resume(config_path, state_path)
    if response.get("decision") != "pass":
        fail_or_retry(config, state_path, state, f"model phase rejected: {response.get('failures')}")
        return 2, load_json(state_path)
    outputs = {}
    expected_paths = {item["name"]: Path(expand(str(item["path"]), config, state_path)).resolve() for item in phase.get("outputs", [])}
    for item in response.get("outputs", []):
        if item.get("name") not in expected_paths or Path(item["path"]).resolve() != expected_paths[item["name"]]:
            raise ValueError(f"model output path mismatch: {item.get('name')}")
        outputs[item["name"]] = verify_ref(item["path"], item["sha256"])
    required = {item["name"] for item in phase.get("outputs", [])}
    if required - set(outputs):
        raise ValueError("model response missing outputs: " + ",".join(sorted(required - set(outputs))))
    complete_phase(config, state_path, state, outputs, f"model response {sha_file(response_path)} verified")
    return resume(config_path, state_path)


def status(config_path: Path, state_path: Path) -> dict:
    config, state = load_bound(config_path, state_path)
    if state.get("active_worker") and int(state.get("phase_index", 0)) < len(config["phases"]):
        phase = config["phases"][int(state["phase_index"])]
        state["worker_running"] = pid_alive(state["active_worker"].get("pid"))
        state["progress"] = progress_snapshot(phase, config, state_path)
    state["front_status"] = front_status_snapshot(config, state)
    return state


def foreground_lease_path(config: dict) -> Path:
    return Path(config["task_root"]) / ".v18" / "foreground_lease.json"


def front_status_path(config: dict) -> Path:
    return Path(config["task_root"]) / ".v18" / "front_status.json"


def age_seconds(value: object) -> float:
    if not isinstance(value, str) or not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return float("inf")


def write_front_status(config: dict, state: dict, run_id: str | None, started_at: str | None, display_status: str) -> dict:
    observed_at = now_iso()
    payload = {
        "schema": FRONT_STATUS_SCHEMA,
        "display_name": str(config.get("display_name", "V18广告收束与真实终检门禁")),
        "task_id": state["task_id"],
        "run_id": run_id,
        "mode": "foreground" if run_id else "idle",
        "status": display_status,
        "started_at": started_at,
        "observed_at": observed_at,
        "elapsed_seconds": None if started_at is None else round(age_seconds(started_at), 3),
        "phase": state.get("current_phase"),
        "task_status": state.get("status"),
        "progress": state.get("last_progress"),
        "lease_path": str(foreground_lease_path(config).resolve()),
        "may_take_over_worker": False,
    }
    atomic_json(front_status_path(config), payload)
    return payload


def front_status_snapshot(config: dict, state: dict) -> dict:
    path = front_status_path(config)
    if not path.is_file():
        return {"schema": FRONT_STATUS_SCHEMA, "display_name": str(config.get("display_name", "V18广告收束与真实终检门禁")), "status": "not_started", "may_take_over_worker": False}
    payload = load_json(path)
    if payload.get("schema") == FRONT_STATUS_SCHEMA and payload.get("started_at"):
        payload["elapsed_seconds"] = round(age_seconds(payload["started_at"]), 3)
        payload["observed_at"] = now_iso()
    return payload


def write_foreground_lease(config: dict, state: dict, mode: str, status_value: str, started_at: str | None = None) -> dict:
    path = foreground_lease_path(config)
    lease = {
        "schema": LEASE_SCHEMA,
        "task_id": state["task_id"],
        "owner_pid": os.getpid() if status_value == "running" else None,
        "mode": mode,
        "status": status_value,
        "started_at": started_at or now_iso(),
        "renewed_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "state_revision": int(state.get("revision", 0)),
        "state_status": state.get("status"),
        "phase": state.get("current_phase"),
    }
    atomic_json(path, lease)
    return lease


def acquire_foreground_lease(config: dict, state: dict, mode: str) -> dict:
    path = foreground_lease_path(config)
    stale_after = float(config["execution"]["heartbeat_stale_after_seconds"])
    if path.is_file():
        existing = load_json(path)
        existing_pid = existing.get("owner_pid")
        if existing.get("schema") == LEASE_SCHEMA and existing.get("status") == "running" and pid_alive(existing_pid) and int(existing_pid) != os.getpid() and age_seconds(existing.get("renewed_at")) <= stale_after:
            raise ValueError("V14_FOREGROUND_ALREADY_ACTIVE")
    return write_foreground_lease(config, state, mode, "running")


def watchdog(config_path: Path, state_path: Path) -> tuple[int, dict]:
    config, state = load_bound(config_path, state_path)
    if state.get("status") == "COMPLETE":
        return 0, {"action": "complete", "state_status": "COMPLETE", "phase": state.get("current_phase")}
    if state.get("status") == "FAILED":
        return 2, {"action": "failed", "state_status": "FAILED", "phase": state.get("current_phase"), "failure": state.get("failure")}
    if state.get("status") == "WAITING_MODEL":
        return 20, {"action": "model_required", "state_status": "WAITING_MODEL", "phase": state.get("current_phase"), "pending_model_action": state.get("pending_model_action")}
    stale_after = float(config["execution"]["heartbeat_stale_after_seconds"])
    path = foreground_lease_path(config)
    lease = load_json(path) if path.is_file() else {}
    lease_fresh = lease.get("schema") == LEASE_SCHEMA and lease.get("status") == "running" and pid_alive(lease.get("owner_pid")) and age_seconds(lease.get("renewed_at")) <= stale_after
    if lease_fresh:
        return 0, {"action": "foreground_active", "owner_pid": lease.get("owner_pid"), "phase": lease.get("phase"), "lease_age_seconds": round(age_seconds(lease.get("renewed_at")), 3)}
    state_age = age_seconds(state.get("updated_at"))
    if state_age <= stale_after:
        return 0, {"action": "recent_progress_no_recovery", "phase": state.get("current_phase"), "state_age_seconds": round(state_age, 3)}
    return 22, {"action": "recovery_required", "phase": state.get("current_phase"), "state_status": state.get("status"), "state_age_seconds": round(state_age, 3), "lease": lease}


def run_continuous(config_path: Path, state_path: Path, budget_seconds: float, recovery: bool = False) -> tuple[int, dict]:
    config, initial = load_bound(config_path, state_path)
    execution = config["execution"]
    if not recovery and execution.get("foreground_required") is True and budget_seconds > 0:
        raise ValueError("V14_BOUNDED_FOREGROUND_FORBIDDEN_USE_RECOVERY_MODE")
    if recovery and budget_seconds <= 0:
        budget_seconds = float(execution["recovery_budget_seconds"])
    deadline = time.monotonic() + budget_seconds if recovery or budget_seconds > 0 else None
    mode = "recovery" if recovery else "foreground"
    lease = acquire_foreground_lease(config, initial, mode)
    run_id = uuid.uuid4().hex
    write_front_status(config, initial, run_id, lease["started_at"], "foreground_active")
    last = initial
    while deadline is None or time.monotonic() < deadline:
        stop_renewal = threading.Event()
        renewal_errors: list[Exception] = []
        renewal_interval = max(0.05, min(30.0, float(execution["heartbeat_stale_after_seconds"]) / 3))
        def renew_while_phase_runs() -> None:
            while not stop_renewal.wait(renewal_interval):
                try:
                    write_foreground_lease(config, last, mode, "running", lease["started_at"])
                except Exception as exc:
                    renewal_errors.append(exc)
                    return
        renewal = threading.Thread(target=renew_while_phase_runs, daemon=True)
        renewal.start()
        try:
            code, last = resume(config_path, state_path)
        finally:
            stop_renewal.set()
            renewal.join()
        if renewal_errors:
            raise RuntimeError("foreground lease renewal failed") from renewal_errors[0]
        terminal = "running" if code == 22 else ("waiting_model" if code == 20 else str(last.get("status", "stopped")).lower())
        write_foreground_lease(config, last, mode, terminal, lease["started_at"])
        write_front_status(config, last, run_id, lease["started_at"], terminal)
        if code != 22:
            return code, last
    write_foreground_lease(config, last, mode, "recovery_slice_complete", lease["started_at"])
    last["status"] = "CONTINUATION_REQUIRED"
    write_front_status(config, last, run_id, lease["started_at"], "recovery_slice_complete")
    return 22, last


def worker(command: list[str], cwd: Path, result_path: Path, stdout_path: Path, stderr_path: Path) -> int:
    started = now_iso()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        try:
            result = subprocess.run(command, cwd=cwd, stdout=out, stderr=err, shell=False)
            code = int(result.returncode)
        except Exception as exc:
            err.write(f"{type(exc).__name__}: {exc}\n")
            code = 127
    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    atomic_json(result_path, {"schema": "semantic-local-phase-result/v260928", "command": command, "started_at": started, "completed_at": now_iso(), "returncode": code, "stdout_path": str(stdout_path.resolve()), "stderr_path": str(stderr_path.resolve()), "stderr_tail": stderr_tail})
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init")
    init_p.add_argument("--config", type=Path, required=True)
    init_p.add_argument("--state", type=Path, required=True)
    resume_p = sub.add_parser("resume")
    resume_p.add_argument("--config", type=Path, required=True)
    resume_p.add_argument("--state", type=Path, required=True)
    continuous_p = sub.add_parser("run-continuous")
    continuous_p.add_argument("--config", type=Path, required=True)
    continuous_p.add_argument("--state", type=Path, required=True)
    continuous_p.add_argument("--budget-seconds", type=float, default=0, help="0 means unbounded foreground execution")
    continuous_p.add_argument("--recovery", action="store_true", help="allow a bounded watchdog recovery slice")
    watchdog_p = sub.add_parser("watchdog")
    watchdog_p.add_argument("--config", type=Path, required=True)
    watchdog_p.add_argument("--state", type=Path, required=True)
    status_p = sub.add_parser("status")
    status_p.add_argument("--config", type=Path, required=True)
    status_p.add_argument("--state", type=Path, required=True)
    front_p = sub.add_parser("front-status")
    front_p.add_argument("--config", type=Path, required=True)
    front_p.add_argument("--state", type=Path, required=True)
    supply_p = sub.add_parser("supply-model")
    supply_p.add_argument("--config", type=Path, required=True)
    supply_p.add_argument("--state", type=Path, required=True)
    supply_p.add_argument("--response", type=Path, required=True)
    worker_p = sub.add_parser("_worker")
    worker_p.add_argument("--command-json", required=True)
    worker_p.add_argument("--cwd", type=Path, required=True)
    worker_p.add_argument("--result", type=Path, required=True)
    worker_p.add_argument("--stdout", type=Path, required=True)
    worker_p.add_argument("--stderr", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_orchestrator(args.config, args.state)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "resume":
            code, result = resume(args.config, args.state)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "progress": result.get("last_progress"), "pending_model_action": result.get("pending_model_action"), "failure": result.get("failure")}, ensure_ascii=False))
            return code
        if args.command == "run-continuous":
            code, result = run_continuous(args.config, args.state, args.budget_seconds, recovery=args.recovery)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "progress": result.get("last_progress"), "pending_model_action": result.get("pending_model_action"), "failure": result.get("failure")}, ensure_ascii=False))
            return code
        if args.command == "watchdog":
            code, result = watchdog(args.config, args.state)
            print(json.dumps(result, ensure_ascii=False))
            return code
        if args.command == "status":
            print(json.dumps(status(args.config, args.state), ensure_ascii=False))
            return 0
        if args.command == "front-status":
            config, state = load_bound(args.config, args.state)
            print(json.dumps(front_status_snapshot(config, state), ensure_ascii=False))
            return 0
        if args.command == "supply-model":
            code, result = supply_model(args.config, args.state, args.response)
            print(json.dumps({"status": result.get("status"), "phase": result.get("current_phase"), "pending_model_action": result.get("pending_model_action")}, ensure_ascii=False))
            return code
        if args.command == "_worker":
            return worker(json.loads(args.command_json), args.cwd, args.result, args.stdout, args.stderr)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 10
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
