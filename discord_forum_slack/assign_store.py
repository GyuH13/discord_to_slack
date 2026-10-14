"""전체 재작성 전에 저장하는 List별 assign 복구 파일."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

BACKUP_DIR = Path("/app/data/assign_backups")


def _path(list_id: str) -> Path:
    return BACKUP_DIR / f"{hashlib.sha256(list_id.encode()).hexdigest()}.json"


def load(list_id: str, column_id: str, kind: str) -> dict[str, list]:
    path = _path(list_id)
    if not path.exists():
        return {}
    # 손상/읽기 실패를 빈 백업으로 취급하면 복구 데이터가 덮어써지므로 예외를 전파한다.
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or any(
        data.get(key) != value
        for key, value in {
            "version": 1,
            "list_id": list_id,
            "column_id": column_id,
            "kind": kind,
        }.items()
    ):
        raise RuntimeError("assign 백업과 현재 열 구성이 다릅니다. 백업을 확인해주세요.")
    assignments = data.get("assignments")
    if not isinstance(assignments, dict) or any(
        not isinstance(value, list)
        or not all(isinstance(v, dict if kind == "rich_text" else str) for v in value)
        for value in assignments.values()
    ):
        raise RuntimeError("assign 백업 형식이 잘못되어 재작성을 중단합니다.")
    return assignments


def save(list_id: str, column_id: str, kind: str, assignments: dict[str, list]) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "list_id": list_id,
        "column_id": column_id,
        "kind": kind,
        "assignments": assignments,
    }
    # 같은 볼륨에서 atomic rename하며, 쓰기 실패 시 기존 정상 백업은 유지한다.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=BACKUP_DIR,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _path(list_id))
        directory_fd = os.open(BACKUP_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
