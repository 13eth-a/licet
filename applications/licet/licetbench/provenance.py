"""content identities for a benchmark even when its git checkout is dirty"""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

SOURCE_DIRECTORIES = ("licet", "licetbench")
SOURCE_SUFFIXES = {".py", ".json", ".md"}
SOURCE_FILES = ("pyproject.toml", "requirements-release.txt")


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def _replay_sources(root):
    files={}
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((root/directory).rglob('*')):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES and '__pycache__' not in path.parts:
                files[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in SOURCE_FILES:
        path=root/name
        if path.is_file():
            files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def provenance(tasks, *, root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    files=_replay_sources(root)
    return {'created_at':datetime.now(timezone.utc).isoformat(), 'source_digest':digest(files),
            'source_files':files,'task_digest':digest([t.as_dict() for t in tasks]),
            'task_manifest':[t.as_dict() for t in tasks]}
