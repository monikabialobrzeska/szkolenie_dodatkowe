"""ADR-003 §8 — viewer rendering."""

from __future__ import annotations

import json
import re

from mapqa.outputs.viewer import render_viewer


def test_render_embeds_data_safely():
    data = {
        "summary": {"source": {"kind": "files"}},
        "candidates": [
            {"candidate_id": "missing_limit:w1", "explanation": "x </script><script>alert(1)</script>",
             "geometry": {"type": "Point", "coordinates": [19.9, 50.0]}},
            {"candidate_id": "sign_road:mly2:w3", "explanation": "y", "geometry": None},
        ],
        "photos": {},
    }
    html = render_viewer(data)
    assert html.count("</script>") == html.count("<script")  # data cannot close the script tag
    payload = re.search(r'<script type="application/json" id="mapqa-data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(payload)["candidates"][0]["candidate_id"] == "missing_limit:w1"
    assert "OpenStreetMap contributors" in html
    assert "leaflet@1.9.4" in html
    assert "No candidates match the filters" in html
    assert "error" not in re.sub(r"<script.*?</script>", "", html, flags=re.S).lower()
