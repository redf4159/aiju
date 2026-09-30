import json, urllib.request, pathlib
token = pathlib.Path("/home/linux/ComfyUI/login/PASSWORD").read_text().splitlines()[0].strip()
req = urllib.request.Request(
    "http://127.0.0.1:8000/object_info",
    headers={"Authorization": "Bearer " + token},
)
d = json.loads(urllib.request.urlopen(req, timeout=60).read())
for name in ("Drama10ShotMiniMaxH3", "Drama10StoryboardMiniMaxH3", "SaveVideo", "SaveImage"):
    print("===", name)
    print(json.dumps(d.get(name, {}), ensure_ascii=False)[:3000])
