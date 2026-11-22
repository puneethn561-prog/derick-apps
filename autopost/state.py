import json
import os

from . import config


def load():
    if os.path.exists(config.STATE_PATH):
        with open(config.STATE_PATH) as fh:
            return json.load(fh)
    return {"items": {}}


def save(state):
    os.makedirs(os.path.dirname(config.STATE_PATH) or ".", exist_ok=True)
    with open(config.STATE_PATH, "w") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False, sort_keys=True)
