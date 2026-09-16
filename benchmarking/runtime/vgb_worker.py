"""Experimental, invocation-owned transport for the pinned wheel API."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from benchmarking.runtime import vgb_bridge as bridge
from benchmarking.runtime.cancellation import BenchmarkCancelledError, CancellationReason, CancellationToken, OwnedProcessRegistry
from benchmarking.runtime.observability import increment, measure

MAX_FRAME_BYTES = 16 * 1024 * 1024

# Reuse the one-shot API's action body, including its track loading and answer
# handling. Only framing/lifetime differs. Native stdout goes to stderr too.
WORKER_API_SCRIPT = r'''
import io, json, os, sys, traceback
protocol_in = sys.stdin
protocol_out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr
identity = json.loads(sys.argv[1])
api = compile(API_SOURCE, "<string>", "exec")
while True:
    line = protocol_in.buffer.readline(MAX_FRAME + 1)
    if not line:
        break
    if len(line) > MAX_FRAME or not line.endswith(b"\n"):
        raise ValueError("worker request frame exceeds limit")
    request = json.loads(line)
    response = {"protocol": 1, "id": request.get("id"), "release": identity}
    try:
        if request.get("protocol") != 1 or request.get("release") != identity:
            raise ValueError("worker request release/protocol mismatch")
        payload = request["payload"]
        if payload.get("action") != "evaluate_one":
            raise ValueError("worker supports evaluate_one only")
        sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False))
        namespace = {}
        exec(api, namespace)
        result = namespace["result"]
        if not isinstance(result, dict):
            raise ValueError("Pinned verifier runtime produced a non-object result")
        response["result"] = result
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        response["error"] = {"code": "verifier_exception", "message": str(exc), "type": type(exc).__name__}
    finally:
        sys.stdin = protocol_in
    encoded = json.dumps(response, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_FRAME:
        encoded = json.dumps({"protocol": 1, "id": request.get("id"), "release": identity, "error": {
            "code": "response_too_large", "message": "worker response frame exceeds limit"}}) + "\n"
    protocol_out.write(encoded)
    protocol_out.flush()
    del response, encoded, request
    # Discard track/result globals before waiting for another request.
    if "namespace" in globals():
        del namespace
    if "result" in globals():
        del result
'''.replace("API_SOURCE", repr(bridge.RUNTIME_API_BODY)).replace("MAX_FRAME", str(MAX_FRAME_BYTES))


class VerifierWorkerError(bridge.VerifierGroundedRuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"Pinned verifier worker failed ({code}): {message}")


class VerifierWorker:
    def __init__(self, config: bridge.ReleaseConfig, *, evidence_root: Path,
                 cancellation_token: CancellationToken,
                 process_registry: OwnedProcessRegistry,
                 validation_cache: bridge.InvocationValidationCache | None = None,
                 max_restarts: int = 2, max_requests: int = 100):
        if max_restarts < 0 or max_requests < 1:
            raise ValueError("invalid verifier worker bounds")
        self.config = deepcopy(config)
        self.evidence_root = Path(evidence_root)
        self.token = cancellation_token
        self.registry = process_registry
        self.cache = validation_cache
        self.max_restarts = max_restarts
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._process = None
        self._fingerprint = None
        self._generation_requests = 0
        self._request_id = 0
        self._starts = 0
        self._failures = 0
        self._successes = 0
        self._cleanup_failed = False

    def _event(self, event: str, **fields: Any) -> None:
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        with (self.evidence_root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": event, "generation": self._starts,
                "request_id": self._request_id, **fields}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _check_active(self) -> None:
        self.token.raise_if_cancelled()
        if self._closing.is_set():
            raise VerifierWorkerError("closed", "worker is closed")

    @contextmanager
    def _request_lock(self):
        while not self._lock.acquire(timeout=0.05):
            self._check_active()
        try:
            yield
        finally:
            self._lock.release()

    def _start(self) -> None:
        self._check_active()
        if self._cleanup_failed:
            raise VerifierWorkerError("cleanup_failed", "previous process cleanup is unresolved")
        if self._failures > self.max_restarts:
            raise VerifierWorkerError("restart_limit", "invocation restart budget exhausted")
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self._starts += 1
        stderr_path = self.evidence_root / f"generation-{self._starts:04d}.stderr.log"
        with stderr_path.open("wb") as stderr:
            self._process = subprocess.Popen(
                [str(self.config.runtime_python), "-I", "-u", "-c", WORKER_API_SCRIPT,
                 json.dumps(self.config.identity)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                cwd=self.config.runtime_root, env=bridge._runtime_env(),
                start_new_session=True, bufsize=0,
            )
        self.registry.register(self._process)
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)
        self._generation_requests = 0
        increment("vgb_process_count")
        increment("vgb_process_count.worker")
        self._event("started", pid=self._process.pid, stderr=str(stderr_path))

    def _stop(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            # Always signal the owned group, even if its leader already exited.
            # Reap an exited leader first: macOS can reject signals to a group
            # containing only an unreaped zombie with EPERM.
            process.poll()
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.25 if sig == signal.SIGTERM else 1.0)
                except subprocess.TimeoutExpired:
                    if sig == signal.SIGKILL:
                        raise
            self._event("stopped", pid=process.pid, returncode=process.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._cleanup_failed = True
            self.token.record_cleanup_error({"stage": "verifier_worker_cleanup", "pid": process.pid,
                                             "error": str(exc)})
            self.token.cancel(CancellationReason(source="verifier_worker_cleanup", message="Verifier cleanup unresolved"))
            raise VerifierWorkerError("cleanup_failed", str(exc)) from exc
        finally:
            for stream in (process.stdin, process.stdout):
                stream.close()
            if not self._cleanup_failed:
                self.registry.unregister(process)
                self._process = None

    def _exchange(self, encoded: bytes, *, timeout: float) -> dict[str, Any]:
        process = self._process
        deadline = time.monotonic() + timeout
        received = bytearray()
        sent = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                self._check_active()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VerifierWorkerError("timeout", f"request exceeded {timeout:g} seconds")
                for key, _ in selector.select(min(0.05, remaining)):
                    if key.fileobj is process.stdin:
                        sent += os.write(process.stdin.fileno(), encoded[sent:sent + 65536])
                        if sent == len(encoded):
                            selector.unregister(process.stdin)
                    else:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise VerifierWorkerError("crashed", "worker closed its response pipe; see stderr evidence")
                        received.extend(chunk)
                        if len(received) > MAX_FRAME_BYTES:
                            raise VerifierWorkerError("response_too_large", "response frame exceeds limit")
                        if b"\n" in received:
                            line, tail = received.split(b"\n", 1)
                            if tail:
                                raise VerifierWorkerError("invalid_response", "unexpected extra response bytes")
                            try:
                                response = json.loads(line)
                            except (ValueError, UnicodeError) as exc:
                                raise VerifierWorkerError("invalid_response", "response is not valid JSON") from exc
                            if (not isinstance(response, dict) or response.get("protocol") != 1
                                    or response.get("id") != self._request_id
                                    or response.get("release") != self.config.identity):
                                raise VerifierWorkerError("invalid_response", "response identity/protocol mismatch")
                            error = response.get("error")
                            if isinstance(error, dict):
                                raise VerifierWorkerError(str(error.get("code") or "verifier_exception"),
                                                          str(error.get("message") or "verifier exception"))
                            if not isinstance(response.get("result"), dict):
                                raise VerifierWorkerError("invalid_response", "response result is not an object")
                            return response["result"]

    def invoke(self, config: bridge.ReleaseConfig, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        with self._request_lock():
            self._check_active()
            if config != self.config or payload.get("action") != "evaluate_one":
                raise VerifierWorkerError("identity_mismatch", "request does not match the invocation release/action")
            self._request_id += 1
            encoded = (json.dumps({"protocol": 1, "id": self._request_id, "release": config.identity,
                                   "payload": payload}, ensure_ascii=False) + "\n").encode("utf-8")
            if len(encoded) > MAX_FRAME_BYTES:
                self._event("failed", code="request_too_large", request_bytes=len(encoded))
                raise VerifierWorkerError("request_too_large", "request frame exceeds limit")
            started = time.monotonic()
            try:
                fingerprint = bridge._validation_fingerprint(config)
                if self._process is not None and (fingerprint != self._fingerprint
                        or self._generation_requests >= self.max_requests):
                    self._event("recycle", reason="fingerprint" if fingerprint != self._fingerprint else "request_limit")
                    self._stop()
                bridge.validate_runtime_files(config, validation_cache=self.cache)
                if fingerprint != bridge._validation_fingerprint(config):
                    raise VerifierWorkerError("runtime_changed", "runtime changed during validation")
                if self._process is None:
                    self._start()
                    self._fingerprint = fingerprint
                self._event("request", track=payload.get("track"), task_id=payload.get("task_id"),
                            request_sha256=hashlib.sha256(encoded).hexdigest(), request_bytes=len(encoded))
                with measure("vgb_worker_request"):
                    result = self._exchange(encoded, timeout=max(0.0, timeout - (time.monotonic() - started)))
                self._check_active()
                self._successes += 1
                self._generation_requests += 1
                self._event("completed", elapsed_seconds=time.monotonic() - started)
                return result
            except (BenchmarkCancelledError, bridge.VerifierGroundedRuntimeError, OSError) as exc:
                self._failures += 1
                code = "cancelled" if isinstance(exc, BenchmarkCancelledError) else getattr(exc, "code", "runtime_error")
                try:
                    self._event("failed", code=code, error=str(exc), elapsed_seconds=time.monotonic() - started)
                finally:
                    self._stop()
                if isinstance(exc, OSError):
                    raise VerifierWorkerError("transport_error", str(exc)) from exc
                raise

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            try:
                self._stop()
            except VerifierWorkerError:
                # The token retains cleanup evidence; final CLI cancellation
                # artifacts must still be written, even on cleanup failure.
                pass

    def to_meta(self) -> dict[str, Any]:
        return {"mode": "worker", "process_count": self._starts, "request_count": self._request_id,
                "completed_count": self._successes, "failure_count": self._failures,
                "max_restarts": self.max_restarts, "max_requests_per_process": self.max_requests,
                "max_frame_bytes": MAX_FRAME_BYTES, "closed": self._closing.is_set(),
                "cleanup_failed": self._cleanup_failed, "evidence_root": str(self.evidence_root)}
