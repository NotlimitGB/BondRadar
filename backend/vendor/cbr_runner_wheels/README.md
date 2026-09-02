# Task257B offline Python wheels

This directory contains the two exact Python wheels required by the
Task257B VDS offline-Python runner build path. It exists because the target
environment cannot reliably download Python packages while building the
runner image.

The normal BondRadar backend dependency contract remains
`backend/requirements.txt`. These wheels are not a replacement package
management system and must not be used for unrelated application builds.

## Approved artifacts

| Package | Wheel | SHA-256 |
|---|---|---|
| `rarfile==4.5` | `rarfile-4.5-py3-none-any.whl` | `c74341f4b9a3a3ebb35ef396d59daf059eb028f34995a7162950a41d97b84de9` |
| `dbfread==2.0.7` | `dbfread-2.0.7-py2.py3-none-any.whl` | `f604def58c59694fa0160d7be5d0b8d594467278d2bb6a47d46daf7162c84cec` |

The artifacts were acquired from the authoritative Python Package Index
release files. Their wheel metadata, byte lengths, and SHA-256 hashes are
verified by the focused packaging test. `SHA256SUMS` is also checked during
the image build before pip runs.

## Build and runtime boundary

From the repository root, the intended external VDS build command is:

```text
docker build --pull=false -f backend/Dockerfile.task257b-local -t bondradar-backend-runner:task257b-local backend
```

The build requires the existing local `bondradar-backend` image and Debian
APT access for `libarchive-tools`. Python installation is restricted to this
directory with `--no-index`, `--require-hashes`, and `--no-deps`.

After the build, runtime verification must use `docker run --rm --network
none`. The resulting image requires no package download or installation at
runtime. This repository task does not perform the VDS build or deployment.
