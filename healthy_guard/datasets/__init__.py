import hashlib
import json
from pathlib import Path

_DIR = Path(__file__).parent


def dataset_sha256(name: str) -> str:
    """Empreinte du fichier : prouve plus tard que le dataset n'a pas été modifié."""
    return hashlib.sha256((_DIR / f"{name}.json").read_bytes()).hexdigest()


def load_dataset(name: str) -> list[dict]:
    """Charge `datasets/<name>.json`, par ex. load_dataset("urgence")."""
    with open(_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)
