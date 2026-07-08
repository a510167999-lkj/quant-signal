import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List


def read_json(path: str, default: Any) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        return default
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, payload: Any) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="%s." % file_path.name,
        suffix=".tmp",
        dir=str(file_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, str(file_path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def append_jsonl(path: str, payload: Dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: str, limit: int = 2000) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    selected = lines[-limit:] if limit > 0 else lines
    items: List[Dict[str, Any]] = []
    for line in selected:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def unique_by_key(items: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for item in items:
        value = item.get(key)
        if value in seen:
            continue
        seen.add(value)
        output.append(item)
    return output
