"""Private task storage and atomic JSON checkpoints."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

def now():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def write(path, data):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary = Path(stream.name)
    temporary.replace(path)

def create(base, parameters):
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = base / uuid4().hex
    run.mkdir(mode=0o700)
    write(run / "state.json", {"id": run.name, "created": now(), "parameters": parameters})
    return run
