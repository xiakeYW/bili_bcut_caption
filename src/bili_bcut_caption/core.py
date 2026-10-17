import copy
import glob
import json
import os
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import pylrc as lrc
import pysrt as srt

# 字幕数据位置
# .bjson
# └── timelineWidget
#    └── timeline
#        └── captionTracks[]        ← 字幕轨数组
#            └── captions[]         ← 每一项 = 一条字幕

DEFAULT_CAPTION_MS = 3000
MIN_CAPTION_MS = 100


@dataclass
class Caption:
    start_ms: int
    end_ms: int
    text: str


def fmt_ms(ms: int) -> str:
    return f"{ms // 60000:02d}:{ms % 60000 // 1000:02d}.{ms % 1000:03d}"


def load_json(file: str):
    with open(file, encoding="utf-8") as f:
        return json.load(f)


def save_json(data, file: str) -> None:
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def print_json(f: dict):
    print(json.dumps(f, indent=2, ensure_ascii=False, sort_keys=True))


def find_latest_bjson(draft_dir: str) -> str | None:
    files = glob.glob(os.path.join(draft_dir, "*.bjson"))
    if not files:
        return None
    return max(files, key=lambda p: (os.path.basename(p), os.path.getmtime(p)))


def parse_subtitles(file: str) -> list[Caption]:
    ext = Path(file).suffix.lower()
    if ext == ".lrc":
        return _parse_lrc(file)
    if ext == ".srt":
        return _parse_srt(file)
    raise ValueError(f"不支持的字幕格式: {ext} (仅支持 .srt / .lrc)")


def _parse_lrc(file: str) -> list[Caption]:
    lines = lrc.parse(Path(file).read_text(encoding="utf-8"))
    items = [
        (round(float(line.time) * 1000), line.text.strip())
        for line in lines
        if line.text.strip()
    ]
    captions = []
    for i, (start, text) in enumerate(items):
        if i + 1 < len(items):
            end = max(items[i + 1][0], start + MIN_CAPTION_MS)
        else:
            end = start + DEFAULT_CAPTION_MS
        captions.append(Caption(start, end, text))
    return captions


def _parse_srt(file: str) -> list[Caption]:
    captions = []
    for item in srt.open(file, encoding="utf-8"):
        text = item.text.strip()
        if not text:
            continue
        start = item.start.ordinal
        end = max(item.end.ordinal, start + MIN_CAPTION_MS)
        captions.append(Caption(start, end, text))
    return captions


def get_caption_tracks(data: dict) -> list[dict]:
    return data.get("timelineWidget", {}).get("timeline", {}).get("captionTracks", [])


def list_template_texts(data: dict) -> list[str]:
    texts = []
    for track in get_caption_tracks(data):
        for cap in track.get("captions", []):
            text = (cap.get("captionText") or "").strip()
            if text and text not in texts:
                texts.append(text)
    return texts


def extract_template(data: dict, template_text: str = "") -> tuple[int, dict] | None:
    candidates = [
        (index, cap)
        for index, track in enumerate(get_caption_tracks(data))
        for cap in track.get("captions", [])
    ]
    if not candidates:
        return None
    template_text = template_text.strip()
    if template_text:
        for index, cap in candidates:
            if (cap.get("captionText") or "").strip() == template_text:
                return index, cap
        return None
    return candidates[0]


def make_id_factory():
    used: set[str] = set()

    def new_id() -> str:
        while True:
            value = str(int(time.time() * 1000)) + f"{random.randint(0, 999999):06d}"
            if value not in used:
                used.add(value)
                return value

    return new_id


def build_caption(template: dict, caption: Caption, new_id) -> dict:
    item = copy.deepcopy(template)
    item["captionText"] = caption.text
    item["inPoint"] = caption.start_ms
    item["outPoint"] = caption.end_ms
    item["idString"] = new_id()
    item["uid"] = new_id()
    asset = item.get("assetInfo")
    if isinstance(asset, dict):
        for key in ("content", "displayName", "itemName"):
            if key in asset:
                asset[key] = caption.text
        if "duration" in asset:
            asset["duration"] = caption.end_ms - caption.start_ms
    return item


def inject_captions(data: dict, track_index: int, captions: list[dict]) -> None:
    tracks = get_caption_tracks(data)
    tracks[track_index]["captions"] = captions


def new_caption_track(data: dict, new_id) -> int:
    tracks = get_caption_tracks(data)
    index = max((track.get("index", 0) for track in tracks), default=0) + 1
    tracks.append({
        "captions": [],
        "compacted": False,
        "enable": True,
        "idString": new_id(),
        "index": index,
        "lock": False,
        "trackType": 3,
        "uid": new_id(),
    })
    return len(tracks) - 1


def backup_project(path: str) -> str | None:
    src = Path(path)
    if not src.is_dir():
        return None
    files = sorted(src.glob("*.bjson"))
    if not files:
        return None
    dest = src / "backup" / time.strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest / f.name)
    return str(dest)


def load_captions(
    bjson_path: str,
    subtitle_path: str,
    style_text: str = "",
    track_index: int = 0,
) -> None:
    data = load_json(bjson_path)
    captions = parse_subtitles(subtitle_path)
    if not captions:
        raise ValueError("字幕文件为空或解析失败")

    template_info = extract_template(data, style_text)
    if not template_info:
        raise RuntimeError("未找到可用的字幕模板，无法加载字幕")

    track_index_tpl, template = template_info
    target_track = track_index_tpl if track_index == 0 else track_index

    id_factory = make_id_factory()
    out_captions = []
    for cap in captions:
        out_captions.append(build_caption(template, cap, id_factory))

    tracks = get_caption_tracks(data)
    if target_track < 0 or target_track >= len(tracks):
        target_track = new_caption_track(data, id_factory)

    inject_captions(data, target_track, out_captions)
    save_json(data, bjson_path)
