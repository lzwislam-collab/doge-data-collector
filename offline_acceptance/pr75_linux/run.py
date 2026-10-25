"""Prepared-only PR75 Linux acceptance runner; invoke explicitly on offline Linux."""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import traceback
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

FIVE = [
    "test_phase2_research.Phase2ResearchTests.test_output_symlink_rejected",
    "test_phase2_research.Phase2ResearchTests.test_symlink_model_cannot_pass_manifest_verification",
    "test_pit_retention_preparation.PreparationTests.test_latest_symlink_cannot_overwrite_outside_file",
    "test_pit_retention_preparation.PreparationTests.test_output_directory_symlink_cannot_redirect_package_writes",
    "test_prediction_release_control.Release.test_release_namespace_parent_symlink_rejected",
]

BUNDLE_FILES = ("run.py", "launch.sh", "manifest.json", "requirements.txt", "README.zh-CN.md", "PREPARED.json", "SHA256SUMS")


def verify_bundle(bundle):
    expected_names = {"offline_acceptance/pr75_linux/" + name for name in BUNDLE_FILES if name != "SHA256SUMS"}
    recorded = {}
    for line in (bundle / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name not in expected_names or name in recorded or len(digest) != 64:
            raise ValueError("invalid/duplicate package checksum entry")
        recorded[name] = digest
        if sha256(reject_links(bundle / Path(name).name)) != digest:
            raise ValueError("package checksum mismatch: " + name)
    if set(recorded) != expected_names:
        raise ValueError("incomplete package checksum list")


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def reject_links(path):
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("linked path/ancestor: " + str(component))
    return path


def publish(path, value):
    """Fully fsync private bytes before no-overwrite hard-link publication."""
    path = reject_links(path)
    if path.exists():
        raise FileExistsError("evidence already exists: " + str(path))
    raw = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".pr75-evidence-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        reject_links(path)
        os.link(temporary, path)
    except BaseException as failure:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except BaseException as cleanup:
                raise BaseExceptionGroup("publication and cleanup failed", [failure, cleanup]) from None
        raise
    else:
        temporary.unlink(missing_ok=True)


def check_inputs(repo, manifest):
    observed = {}
    for name, expected in manifest["source_sha256_lf"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe manifest path")
        path = reject_links(repo / relative)
        raw = path.read_bytes()
        observed["raw:" + name] = hashlib.sha256(raw).hexdigest()
        observed[name] = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        if observed[name] != expected:
            raise ValueError("source/config digest mismatch: " + name)
    for name, expected in manifest["frozen_files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe frozen path")
        path = reject_links(repo / relative)
        raw = path.read_bytes()
        observed["raw-frozen:" + name] = hashlib.sha256(raw).hexdigest()
        if name not in manifest["frozen_binary_exact"]:
            raw = raw.replace(b"\r\n", b"\n")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError("frozen digest mismatch: " + name)
        observed["frozen:" + name] = actual
    return observed


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, progress, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed = []
        self.passed = []
        progress["started_ids"] = self.executed
        progress["passed_ids"] = self.passed

    def startTest(self, test):
        self.executed.append(test.id())
        super().startTest(test)

    def addSuccess(self, test):
        self.passed.append(test.id())
        super().addSuccess(test)


def network_guard():
    stack = contextlib.ExitStack()
    for target, names in [
        (socket.socket, ["connect", "connect_ex", "sendto", "sendmsg"]),
        (socket, ["getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"]),
    ]:
        for name in names:
            if hasattr(target, name):
                stack.enter_context(patch.object(target, name, side_effect=AssertionError("offline: network forbidden")))
    return stack


def execute(repo, output, manifest, report):
    report["phase"] = "environment_preflight"
    if sys.implementation.name != "cpython" or platform.python_version() != "3.12.14":
        raise RuntimeError("pre-provisioned CPython 3.12.14 required")
    if not sys.flags.isolated or not sys.dont_write_bytecode or not sys.flags.utf8_mode or sys.flags.optimize:
        raise RuntimeError("invoke Python with -I -B -X utf8, without -O")
    if not sys.platform.startswith("linux") or os.name != "posix" or platform.system() != "Linux":
        raise RuntimeError("actual Linux required; this is not a Windows simulation")
    report["inputs_before"] = check_inputs(repo, manifest)
    sys.path.insert(0, str(repo))
    import numpy
    if numpy.__version__ != "2.3.5":
        raise RuntimeError("pre-provisioned NumPy 2.3.5 required")
    report["environment"].update(numpy_version=numpy.__version__, numpy_path=numpy.__file__)
    report["environment"]["git_version"] = subprocess.check_output(["git", "--version"], text=True).strip()
    report["environment"]["git_head"] = subprocess.check_output(
        ["git", "-c", "safe.directory=*", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    publish(output / "environment.json", report["environment"])
    with tempfile.TemporaryDirectory(prefix="probe-", dir=output / "temporary") as folder:
        probe = Path(folder)
        (probe / "file").write_bytes(b"private fixture")
        (probe / "directory").mkdir()
        (probe / "file-link").symlink_to(probe / "file")
        (probe / "directory-link").symlink_to(probe / "directory", target_is_directory=True)
        if not (probe / "file-link").is_symlink() or not (probe / "directory-link").is_symlink():
            raise RuntimeError("real file/directory symlink probe failed")
    report["real_symlink_probe"] = True
    with network_guard():
        report["python_network_guard"] = True
        report["phase"] = "five_original_tests"
        report["five_progress"] = {}
        with (output / "five-tests.log").open("x", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=lambda *a, **kw: RecordedResult(
                    *a, progress=report["five_progress"], **kw)).run(
                    unittest.defaultTestLoader.loadTestsFromNames(FIVE))
        five = {"tests_run": result.testsRun, "executed": result.executed, "passed": result.passed,
                "failures": [[str(t), trace] for t, trace in result.failures],
                "errors": [[str(t), trace] for t, trace in result.errors],
                "skipped": [[str(t), reason] for t, reason in result.skipped]}
        five["verified"] = (result.wasSuccessful() and not result.skipped and result.testsRun == 5
                            and result.executed == FIVE and result.passed == FIVE)
        publish(output / "five-tests.json", five)
        report["five_tests_verified"] = five["verified"]
        report["phase"] = "strict_inventory_tool"
        import offline_linux_inventory_checks as strict
        strict_error = None
        strict_executed = []
        original_start = unittest.TextTestResult.startTest

        def observe_start(result, test):
            strict_executed.append(test.id())
            return original_start(result, test)

        try:
            with (output / "strict-inventory.log").open("x", encoding="utf-8") as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    with patch.object(unittest.TextTestResult, "startTest", observe_start), patch.object(
                        sys, "argv", ["offline_linux_inventory_checks.py", "--output", str(output / "strict-inventory.json")]
                    ):
                        strict.main()
        except SystemExit as error:
            strict_error = str(error)
        finally:
            report["strict_tool_error"] = strict_error
            report["strict_executed_ids"] = strict_executed
        strict_result = json.loads((output / "strict-inventory.json").read_bytes())
        report["strict_tool_verified"] = (strict_error is None and strict_result.get("linux_verified") is True
                                          and strict_result.get("platform", "").startswith("linux")
                                          and strict_result.get("tests_run") == 4
                                          and strict_executed == manifest["strict_tests"]
                                          and all(strict_result.get(name) == 0 for name in ("failures", "errors", "skipped")))
        report["observed_total_executions"] = len(result.executed) + len(strict_executed)
        report["observed_unique_tests"] = len(set(result.executed + strict_executed))
        report["phase"] = "tests_completed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).absolute().parents[2])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = reject_links(args.repo)
    output = reject_links(args.output_dir)
    if output.is_relative_to(repo) or repo.is_relative_to(output):
        raise ValueError("evidence output must be a new directory outside the checkout")
    if not output.parent.is_dir():
        raise ValueError("output parent must already exist")
    output.mkdir(mode=0o700)
    (output / "temporary").mkdir(mode=0o700)
    bundle = Path(__file__).absolute().parent
    report = {"schema": "PR75_LINUX_OFFLINE_ACCEPTANCE_V1", "status": "NOT_VERIFIED", "linux_verified": False,
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "repository": str(repo),
              "expected_five_tests": FIVE, "expected_strict_executions": 4,
              "expected_total_executions": 9, "expected_unique_tests": 7,
              "python_network_guard": False, "kernel_network_isolation_verified": False,
              "environment": {"platform": sys.platform, "system": platform.system(), "kernel": platform.release(),
                              "python_version": platform.python_version(), "python_executable": sys.executable},
              "production_operations": False}
    original_tempdir = tempfile.tempdir
    tempfile.tempdir = str(output / "temporary")
    try:
        report["phase"] = "bundle_preflight"
        if reject_links(bundle) != repo / "offline_acceptance/pr75_linux":
            raise ValueError("runner must belong to the specified repository")
        report["bundle_before"] = {name: sha256(reject_links(bundle / name)) for name in BUNDLE_FILES}
        verify_bundle(bundle)
        report["bundle_checksums_verified"] = True
        manifest = json.loads((bundle / "manifest.json").read_bytes())
        if manifest.get("schema") != "PR75_LINUX_SOURCE_MANIFEST_V1" or manifest.get("five_tests") != FIVE:
            raise ValueError("wrong package manifest/test set")
        with contextlib.chdir(repo), patch.dict(os.environ, {"GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}):
            execute(repo, output, manifest, report)
    except BaseException as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        tempfile.tempdir = original_tempdir
        if "inputs_before" in report:
            try:
                report["inputs_after"] = check_inputs(repo, manifest)
                report["inputs_unchanged"] = report["inputs_before"] == report["inputs_after"]
            except BaseException as error:
                report["inputs_unchanged"] = False
                report["input_postcheck_error"] = {"type": type(error).__name__, "error": str(error)}
        if "bundle_before" in report:
            try:
                report["bundle_after"] = {name: sha256(reject_links(bundle / name)) for name in report["bundle_before"]}
                report["bundle_unchanged"] = report["bundle_before"] == report["bundle_after"]
            except BaseException as error:
                report["bundle_unchanged"] = False
                report["bundle_postcheck_error"] = {"type": type(error).__name__, "error": str(error)}
        try:
            report["temporary_residue"] = sorted(str(p.relative_to(output)) for p in (output / "temporary").iterdir())
            report["temporary_clean"] = not report["temporary_residue"]
        except BaseException as error:
            report["temporary_clean"] = False
            report["temporary_inspection_error"] = {"type": type(error).__name__, "error": str(error)}
    names = ["environment.json", "five-tests.log", "five-tests.json", "strict-inventory.log", "strict-inventory.json"]
    evidence_hashes = {}
    report["evidence_collection_errors"] = []
    for name in names:
        try:
            path = reject_links(output / name)
            if not path.is_file():
                raise FileNotFoundError("required evidence missing: " + name)
            evidence_hashes[name] = sha256(path)
        except BaseException as error:
            report["evidence_collection_errors"].append({"file": name, "type": type(error).__name__, "error": str(error)})
    report["required_evidence_complete"] = not report["evidence_collection_errors"]
    report["linux_verified"] = ("error_type" not in report and all(report.get(name) is True for name in (
        "five_tests_verified", "strict_tool_verified", "inputs_unchanged", "bundle_unchanged", "real_symlink_probe",
        "bundle_checksums_verified", "python_network_guard", "temporary_clean", "required_evidence_complete"))
        and report.get("observed_total_executions") == 9 and report.get("observed_unique_tests") == 7)
    if report["linux_verified"]:
        report["status"] = "PASS"
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    publish(output / "summary.json", report)
    evidence_hashes["summary.json"] = sha256(reject_links(output / "summary.json"))
    publish(output / "evidence-sha256.json", evidence_hashes)
    print(json.dumps({"status": report["status"], "output": str(output)}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
