"""Single application identity shared with the web interface and installer."""
import json
from pathlib import Path

APPLICATION = json.loads((Path(__file__).resolve().parents[2] / "data/application.json").read_text(encoding="utf-8"))
NAME = APPLICATION["name"]
SUBTITLE = APPLICATION["subtitle"]
FULL_TITLE = APPLICATION["full_title"]
VERSION = APPLICATION["version"]
