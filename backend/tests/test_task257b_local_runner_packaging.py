from __future__ import annotations

import hashlib
import zipfile
from email.parser import Parser
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
WHEEL_ROOT = BACKEND_ROOT / "vendor" / "cbr_runner_wheels"
DOCKERFILE = BACKEND_ROOT / "Dockerfile.task257b-local"

EXPECTED_WHEELS = {
    "rarfile-4.5-py3-none-any.whl": {
        "name": "rarfile",
        "version": "4.5",
        "size": 30_035,
        "sha256": "c74341f4b9a3a3ebb35ef396d59daf059eb028f34995a7162950a41d97b84de9",
    },
    "dbfread-2.0.7-py2.py3-none-any.whl": {
        "name": "dbfread",
        "version": "2.0.7",
        "size": 20_018,
        "sha256": "f604def58c59694fa0160d7be5d0b8d594467278d2bb6a47d46daf7162c84cec",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(path: Path):
    with zipfile.ZipFile(path) as wheel:
        metadata_paths = [
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_paths) == 1
        payload = wheel.read(metadata_paths[0]).decode("utf-8")
    return Parser().parsestr(payload)


def test_vendored_wheels_have_exact_bytes_metadata_and_hash_contract() -> None:
    wheels = {path.name: path for path in WHEEL_ROOT.glob("*.whl")}
    assert set(wheels) == set(EXPECTED_WHEELS)

    expected_sums: list[str] = []
    expected_requirements: list[str] = []
    for filename, expected in EXPECTED_WHEELS.items():
        wheel = wheels[filename]
        assert wheel.stat().st_size == expected["size"]
        assert _sha256(wheel) == expected["sha256"]

        metadata = _metadata(wheel)
        assert metadata["Name"].lower() == expected["name"]
        assert metadata["Version"] == expected["version"]
        assert metadata.get_all("Requires-Dist") is None

        expected_sums.append(f"{expected['sha256']}  {filename}")
        expected_requirements.append(
            f"{expected['name']}=={expected['version']} "
            f"--hash=sha256:{expected['sha256']}"
        )

    assert (WHEEL_ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines() == (
        expected_sums
    )
    assert (WHEEL_ROOT / "requirements.offline.txt").read_text(
        encoding="ascii"
    ).splitlines() == expected_requirements

    readme = (WHEEL_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Task257B VDS offline-Python runner build path" in readme
    assert "backend/requirements.txt" in readme
    for expected in EXPECTED_WHEELS.values():
        assert expected["sha256"] in readme


def test_dedicated_dockerfile_is_local_base_and_offline_python_only() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in dockerfile.splitlines() if line.strip()]

    assert lines[0] == "FROM bondradar-backend"
    assert sum(line.startswith("FROM ") for line in lines) == 1
    assert "apt-get install -y --no-install-recommends libarchive-tools" in dockerfile
    assert "COPY vendor/cbr_runner_wheels/ /opt/cbr_runner_wheels/" in dockerfile
    assert "sha256sum --check SHA256SUMS" in dockerfile
    assert "--no-index" in dockerfile
    assert "--find-links=/opt/cbr_runner_wheels" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--no-deps" in dockerfile
    assert "COPY . ." in dockerfile
    assert dockerfile.index("requirements.offline.txt") < dockerfile.index("COPY . .")

    lowered = dockerfile.lower()
    for forbidden in (
        "from python:",
        "from ghcr.io/",
        "from docker.io/",
        "pypi",
        "extra-index-url",
        "index-url",
        "curl",
        "wget",
    ):
        assert forbidden not in lowered
    assert not any(line.startswith(("CMD ", "ENTRYPOINT ")) for line in lines)

    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "rarfile==4.5" in requirements
    assert "dbfread==2.0.7" in requirements

    dockerignore = (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "vendor" not in dockerignore.lower()
