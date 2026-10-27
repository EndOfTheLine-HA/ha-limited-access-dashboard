"""Loopback-only UI fixture. No HA connection or real device actions.

Run: python tests/preview_editor.py --port 8768
Owner editor: http://127.0.0.1:8768/editor
Guest preview: http://127.0.0.1:8768/limited-access-dashboard/demo
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parents[1] / "custom_components/limited_access_dashboard"
BASE = "/limited-access-dashboard/demo"
CARDS = [
    {
        "entity_id": entity,
        "name": name,
        "state": state,
        "unit": unit,
        "active": state == "On",
        "available": True,
        "action": action,
        "card_type": "control",
        "icon": "◉",
        "media_controls": [],
        "volume": 30,
    }
    for entity, name, state, unit, action in [
        ("light.guest", "Guest room light", "On", "", "toggle"),
        ("switch.patio", "Patio plug", "Off", "", "toggle"),
        ("sensor.temperature", "Room temperature", "21", "°C", None),
    ]
]
DOCUMENT = {
    "allow_guest_customization": True,
    "resources": ["/local/lad-test-card.js"],
    "entities": CARDS,
    "url": BASE,
    "csrf_token": "fixture-only",
    "design": {
        "theme": "dark",
        "accent": "#03a9f4",
        "background": "#11131a",
        "card_background": "#1c1f2a",
        "columns": 3,
        "density": "comfortable",
        "views": [
            {
                "title": "Lights & plugs",
                "sections": [
                    {
                        "title": "Guest room",
                        "cards": [
                            {"type": "entity", "entity": "light.guest"},
                            {"type": "entity", "entity": "switch.patio"},
                        ],
                    }
                ],
            },
            {
                "title": "Sensors",
                "sections": [
                    {
                        "title": "Comfort",
                        "cards": [
                            {
                                "type": "lovelace",
                                "config": {
                                    "type": "custom:lad-test-card",
                                    "entity": "sensor.temperature",
                                },
                            },
                            {"type": "entity", "entity": "sensor.temperature"},
                        ],
                    }
                ],
            },
        ],
    },
}
ENV = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/editor":
            return self.send(
                """<!doctype html><html><body style="margin:0;background:#11131a;color:#e1e1e6">
<lad-editor-panel></lad-editor-panel><script type="module">
import '/limited-access-dashboard/editor-assets/editor-panel.js';
document.querySelector('lad-editor-panel').hass={user:{is_owner:true},callWS:async msg=>{
if(!msg.entry_id)return [{entry_id:'demo',title:'Demo dashboard'}];
if(msg.document)await fetch('/fixture/document',{method:'POST',body:JSON.stringify(msg.document)});
return (await fetch('/fixture/document')).json();}};
</script></body></html>""",
                "text/html",
            )
        if path == BASE:
            return self.send(
                ENV.get_template("dashboard.html").render(
                    title="Guest house",
                    base_path=BASE,
                    account={"display_name": "Demo guest"},
                    csrf_token="fixture-only",  # noqa: S106
                    cards=CARDS,
                    available=True,
                    has_sprinklers=False,
                ),
                "text/html",
            )
        if path in {"/fixture/document", f"{BASE}/api/design"}:
            return self.send(json.dumps(DOCUMENT), "application/json")
        if path == f"{BASE}/api/state":
            return self.send(
                json.dumps(
                    {
                        "available": True,
                        "cards": CARDS,
                        "states": {
                            c["entity_id"]: {
                                "entity_id": c["entity_id"],
                                "state": c["state"].lower(),
                                "attributes": {
                                    "friendly_name": c["name"],
                                    "unit_of_measurement": c["unit"],
                                },
                            }
                            for c in CARDS
                        },
                    }
                ),
                "application/json",
            )
        if path.startswith((f"{BASE}/static/", "/limited-access-dashboard/editor-assets/")):
            filename = path.rsplit("/", 1)[-1]
            if filename in {"app.css", "app.js", "designer.js", "cards.js", "editor-panel.js"}:
                return self.send(
                    (ROOT / "static" / filename).read_text(encoding="utf-8"),
                    "text/css" if filename.endswith(".css") else "text/javascript",
                )
        if path == "/local/lad-test-card.js":
            return self.send(
                """customElements.define('lad-test-card', class extends HTMLElement {
setConfig(config){this.config=config;} set hass(hass){const state=hass.states[this.config.entity];
this.textContent=`Custom HACS fixture: ${state?.attributes.friendly_name}: ${state?.state}`;
this.style.cssText='display:block;padding:24px;border:1px solid #03a9f4;border-radius:14px';}});""",
                "text/javascript",
            )
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/fixture/document":
            DOCUMENT.update(data)
        elif self.path == f"{BASE}/api/design":
            DOCUMENT["design"] = data
        else:
            return self.send_error(400, "Fixture does not operate devices")
        self.send('{"saved":true}', "application/json")

    def send(self, content, content_type):
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    print(f"Fixture: http://127.0.0.1:{args.port}/editor", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
