"""Release format and safe materialization. No cloud dependencies at import time."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from agent_factory import DCODE_VERSION

MAX_FILES = 512
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class DefinitionError(ValueError):
    """An invalid or incompatible release. Safe to report to the caller."""


def identifier(value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise DefinitionError("Use lowercase names: letters, digits and hyphens (1-63).")
    return value


def safe_path(value: str) -> str:
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise DefinitionError("Invalid release path.")
    parts = PurePosixPath(value).parts
    if (
        not value.startswith("profile/")
        or not parts
        or any(p in {".", "..", ".state", ".env", "memories"} for p in parts)
        or any(p.startswith(".env.") for p in parts)
        or str(PurePosixPath(value)) != value
    ):
        raise DefinitionError("Release files must be normalized profile paths without state or secrets.")
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Objects(Protocol):
    def read(self, key: str, maximum: int) -> bytes: ...


class S3Objects:
    def __init__(self, bucket: str):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3")

    def read(self, key: str, maximum: int) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            if response["ContentLength"] > maximum:
                raise DefinitionError("Object exceeds the release size limit.")
            value = body.read(maximum + 1)
            if len(value) > maximum:
                raise DefinitionError("Object exceeds the release size limit.")
            return value
        finally:
            body.close()


class LocalObjects:
    """Local development only; production uses S3Objects."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def read(self, key: str, maximum: int) -> bytes:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise DefinitionError("Invalid local object key.")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise DefinitionError("Object exceeds the release size limit.")
        return data


@dataclass(frozen=True)
class Release:
    team: str
    version: str
    model: str
    files: dict[str, str]
    manifest_digest: str

    @classmethod
    def parse(cls, raw: bytes, team: str, version: str) -> Release:
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DefinitionError("Invalid manifest JSON.") from exc
        if not isinstance(data, dict) or set(data) != {
            "schema_version", "dcode_version", "team", "version", "model", "files"
        }:
            raise DefinitionError("Manifest fields do not match schema v1.")
        if data["schema_version"] != 1 or data["dcode_version"] != DCODE_VERSION:
            raise DefinitionError("Incompatible schema or dcode version.")
        if data["team"] != identifier(team) or data["version"] != identifier(version):
            raise DefinitionError("Manifest team/version does not match the requested release.")
        model, files = data["model"], data["files"]
        if not isinstance(model, str) or not 3 <= len(model) <= 512 or ":" not in model:
            raise DefinitionError("Model must use provider:model syntax.")
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
            raise DefinitionError("Invalid release file count.")
        for path, sha in files.items():
            safe_path(path)
            if not isinstance(sha, str) or not SHA256.fullmatch(sha):
                raise DefinitionError("Invalid file SHA-256.")
        if f"profile/{team}/AGENTS.md" not in files:
            raise DefinitionError("Missing principal agent AGENTS.md.")
        if sum(len(k) for k in files) > MAX_MANIFEST_BYTES:
            raise DefinitionError("Manifest is too large.")
        return cls(team, version, model, files, digest(raw))


class BundleLoader:
    def __init__(self, objects: Objects, prefix: str = "teams"):
        if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise DefinitionError("Invalid definitions prefix.")
        self.objects = objects
        self.prefix = prefix.rstrip("/")

    def materialize(self, root: Path, team: str, version: str) -> Release:
        key = f"{self.prefix}/{identifier(team)}/releases/{identifier(version)}"
        raw = self.objects.read(f"{key}/manifest.json", MAX_MANIFEST_BYTES)
        release = Release.parse(raw, team, version)
        root.mkdir(parents=True, exist_ok=True)
        binding = {
            "team": team, "version": version, "manifest_sha256": release.manifest_digest
        }
        bound_file = root / "binding.json"
        if bound_file.exists():
            if json.loads(bound_file.read_text()) != binding:
                raise DefinitionError("Runtime session is already pinned to another release.")
            if not (root / "profile" / team / "AGENTS.md").is_file():
                raise DefinitionError("Pinned profile is missing; use a new runtime session.")
            return release  # Never overwrite writable memory/checkpoints on reconnect.
        staging = Path(tempfile.mkdtemp(prefix=".release-", dir=root))
        try:
            total = 0
            for relative, expected in release.files.items():
                content = self.objects.read(f"{key}/{relative}", MAX_FILE_BYTES)
                total += len(content)
                if total > MAX_TOTAL_BYTES:
                    raise DefinitionError("Release exceeds the total size limit.")
                if digest(content) != expected:
                    raise DefinitionError("Release file checksum does not match its manifest.")
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            # A previous interrupted first load is replaced only before a binding exists.
            shutil.rmtree(root / "profile", ignore_errors=True)
            os.replace(staging / "profile", root / "profile")
            atomic_json(bound_file, binding)
            (root / "project").mkdir(exist_ok=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return release


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def pack(source: Path, team: str, version: str, model: str) -> tuple[bytes, dict[str, bytes]]:
    source = source.resolve()
    files: dict[str, bytes] = {}
    for path in sorted((source / "profile").rglob("*")):
        if path.is_symlink():
            raise DefinitionError("Release symlinks are not allowed.")
        if path.is_file():
            relative = safe_path(path.relative_to(source).as_posix())
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise DefinitionError("Release file is too large.")
            files[relative] = data
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise DefinitionError("Release exceeds the total size limit.")
    raw = json.dumps({
        "schema_version": 1, "dcode_version": DCODE_VERSION,
        "team": identifier(team), "version": identifier(version), "model": model,
        "files": {path: digest(value) for path, value in files.items()},
    }, indent=2, sort_keys=True).encode() + b"\n"
    if len(raw) > MAX_MANIFEST_BYTES:
        raise DefinitionError("Manifest is too large.")
    Release.parse(raw, team, version)
    return raw, files
