import requests
import json
import hashlib
import re
import time
import os
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ─────────────────────────────────────────────────────────────────────────────
# TIMEZONE
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ       = timezone(timedelta(hours=7))
LIVE_BEFORE = timedelta(minutes=15)


def now_vn() -> datetime:
    return datetime.now(tz=VN_TZ)


def utc_to_vn(utc_str: str) -> datetime | None:
    """Parse ISO UTC string → datetime aware (VN tz)."""
    if not utc_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(utc_str.rstrip("Z"), fmt.rstrip("Z"))
            return dt.replace(tzinfo=timezone.utc).astimezone(VN_TZ)
        except ValueError:
            pass
    return None


def format_vn_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%H:%M %d/%m/%Y")


def calc_is_live(api_live_flag: bool, start_dt: datetime | None) -> bool:
    if api_live_flag:
        return True
    if start_dt is None:
        return False
    return now_vn() >= (start_dt - LIVE_BEFORE)


def is_within_24h(start_dt: datetime | None, sport_slug: str) -> bool:
    """Bóng đá: chỉ hiển thị trận trong 24h tới và tối đa 6h đã qua. Môn khác: True luôn."""
    if sport_slug != "bong-da":
        return True
    if start_dt is None:
        return True
    now   = now_vn()
    lower = now - timedelta(hours=6)
    upper = now + timedelta(hours=24)
    return lower <= start_dt <= upper


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

API_BASE      = "https://sv.hoiquantv.xyz/api/v1/external/fixtures"
API_UNFINISHED = f"{API_BASE}/unfinished"
API_FINISHED   = f"{API_BASE}/finished"

THUMBS_DIR  = "thumbs"
REPO_RAW    = os.environ.get("REPO_RAW", "")
THUMB_VERSION = "v1"

# sport.slug → emoji + tên hiển thị
SPORT_MAP = {
    "bong-da":    ("⚽", "Bóng Đá"),
    "bong-ro":    ("🏀", "Bóng Rổ"),
    "billiards":  ("🎱", "Billiards"),
    "cau-long":   ("🏸", "Cầu Lông"),
    "tennis":     ("🎾", "Tennis"),
    "bong-chuyen":("🏐", "Bóng Chuyền"),
    "bong-ban":   ("🏓", "Bóng Bàn"),
    "vo-thuat":   ("🥊", "Võ Thuật"),
}

EXCLUDE_LEAGUES_AMERICA = [
    "mls", "major league soccer",
    "liga mx", "liga de expansion",
    "brasileirao", "brasileirão", "serie a brasil", "campeonato brasileiro", "brazilian",
    "copa do brasil",
    "argentine", "argentina", "liga profesional", "copa de la liga",
    "colombian", "colombia", "liga betplay", "categoria primera", "primera a",
    "chile", "primera division chile",
    "ecuador", "liga pro ecuador",
    "peru", "liga 1 peru", "liga 1 perú",
    "venezuela", "liga futve",
    "paraguay", "apertura paraguay",
    "uruguay", "primera division uruguay",
    "bolivia", "division profesional",
    "inter miami", "new england", "la galaxy", "nycfc",
    "concacaf", "conmebol",
    "copa america", "copa sudamericana", "copa libertadores",
    "jupiler", "pro league", "first division a", "belgian",
    "efbet league", "parva liga", "bulgarian",
    "super lig", "tff", "turkish", "süper lig",
]


def is_america_league(league_name: str) -> bool:
    lower = league_name.lower()
    return any(kw in lower for kw in EXCLUDE_LEAGUES_AMERICA)


def make_id(text: str, prefix: str) -> str:
    h = hashlib.md5(text.encode()).hexdigest()[:10]
    return f"{prefix}-{h}"


# ─────────────────────────────────────────────────────────────────────────────
# FETCH API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fixtures(url: str) -> list:
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        data = res.json()
        # API có thể trả về list hoặc {"data": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", data.get("fixtures", data.get("items", [])))
        return []
    except Exception as e:
        print(f"  Lỗi fetch {url}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# PARSE MATCHES
# ─────────────────────────────────────────────────────────────────────────────

def get_matches() -> list:
    print("Lấy danh sách trận chưa kết thúc...")
    raw = fetch_fixtures(API_UNFINISHED)

    # Lấy set ID trận đã xong để loại bỏ (nếu cần)
    # finished = {str(f.get("id")) for f in fetch_fixtures(API_FINISHED)}

    matches = []
    seen    = set()

    for f in raw:
        match_id = str(f.get("id", ""))
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)

        # ── Sport ─────────────────────────────────────────────────────────────
        sport     = f.get("sport", {}) or {}
        sport_slug = sport.get("slug", "")
        sport_priority = sport.get("priority", 99)

        # ── Thời gian ─────────────────────────────────────────────────────────
        start_dt  = utc_to_vn(f.get("startTime", ""))
        time_str  = format_vn_time(start_dt)
        time_sort = (
            start_dt.month * 10_000_000 + start_dt.day * 10_000
            + start_dt.hour * 100 + start_dt.minute
            if start_dt else 999_999_999
        )

        # ── Lọc 24h (bóng đá) ─────────────────────────────────────────────────
        if not is_within_24h(start_dt, sport_slug):
            continue

        # ── League ────────────────────────────────────────────────────────────
        league_obj  = f.get("league", {}) or {}
        league_name = league_obj.get("name", "")
        league_logo = league_obj.get("logoUrl", "")

        # Bỏ giải châu Mỹ (chỉ bóng đá)
        if sport_slug == "bong-da" and is_america_league(league_name):
            continue

        # ── Đội ───────────────────────────────────────────────────────────────
        home = f.get("homeTeam", {}) or {}
        away = f.get("awayTeam", {}) or {}
        team_a  = home.get("name", "")
        logo_a  = home.get("logoUrl", "")
        team_b  = away.get("name", "")
        logo_b  = away.get("logoUrl", "")

        name = f"{team_a} vs {team_b}" if team_a and team_b else f"Trận #{match_id}"

        # ── Commentators & Streams ────────────────────────────────────────────
        commentators = f.get("fixtureCommentators", []) or []
        blv_list = []
        for c in commentators:
            cmt    = c.get("commentator", {}) or {}
            blv_nm = cmt.get("name", "").strip()
            streams_obj = c.get("streams", {}) or {}
            # Chỉ lấy FHD
            fhd_url = (
                streams_obj.get("FHD", {}) or {}
            ).get("sourceUrl", "") or (
                streams_obj.get("fhd", {}) or {}
            ).get("sourceUrl", "")
            if blv_nm and fhd_url:
                blv_list.append({"name": blv_nm, "fhd": fhd_url})

        # Bỏ trận không có BLV / stream
        if not blv_list:
            continue

        blv_names = ", ".join(b["name"] for b in blv_list)

        # ── is_live ────────────────────────────────────────────────────────────
        is_live = calc_is_live(bool(f.get("isLive", False)), start_dt)

        matches.append({
            "match_id":       match_id,
            "name":           name,
            "time":           time_str,
            "time_sort":      time_sort,
            "team_a":         team_a,
            "team_b":         team_b,
            "logo_a":         logo_a,
            "logo_b":         logo_b,
            "league":         league_name,
            "league_logo":    league_logo,
            "blv":            blv_names,
            "blv_list":       blv_list,   # [{"name": str, "fhd": str}]
            "is_live":        is_live,
            "sport_slug":     sport_slug,
            "sport_priority": sport_priority,
        })

    # LIVE lên đầu → sort theo giờ tăng dần
    matches.sort(key=lambda m: (0 if m["is_live"] else 1, m["time_sort"]))
    return matches


# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────────────────────

def fetch_image(url: str):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None


def cleanup_old_thumbs(days: int = 3):
    if not os.path.exists(THUMBS_DIR):
        return
    cutoff  = now_vn() - timedelta(days=days)
    removed = 0
    for fname in os.listdir(THUMBS_DIR):
        if not fname.endswith(".png"):
            continue
        m = re.search(r'_(\d{8})\.png$', fname)
        if not m:
            fpath = os.path.join(THUMBS_DIR, fname)
            try:
                os.remove(fpath)
                removed += 1
            except Exception as e:
                print(f"  Lỗi xoá thumb {fname}: {e}")
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=VN_TZ)
        except ValueError:
            continue
        if file_date < cutoff:
            fpath = os.path.join(THUMBS_DIR, fname)
            try:
                os.remove(fpath)
                removed += 1
            except Exception as e:
                print(f"  Lỗi xoá thumb {fname}: {e}")
    if removed:
        print(f"Đã xoá {removed} thumbnail cũ (>{days} ngày)")


def make_thumbnail(match: dict, channel_id: str) -> str:
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cache_key = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VERSION
    logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    date_str  = now_vn().strftime("%Y%m%d")
    out_path  = f"{THUMBS_DIR}/{channel_id}_{logo_hash}_{date_str}.png"

    if os.path.exists(out_path):
        return out_path

    W, H     = 1600, 1200
    HEADER_H = 180
    FOOTER_H = 160

    bg   = Image.new("RGB", (W, H), (245, 245, 248))
    draw = ImageDraw.Draw(bg)

    for y in range(HEADER_H, H - FOOTER_H):
        ratio = (y - HEADER_H) / (H - FOOTER_H - HEADER_H)
        gray  = int(248 - ratio * 18)
        draw.line([(0, y), (W, y)], fill=(gray, gray, gray + 4))

    draw.rectangle([(0, 0),            (W, HEADER_H)],  fill=(13, 20, 40))
    draw.rectangle([(0, H - FOOTER_H), (W, H)],         fill=(13, 20, 40))

    ACCENT = (220, 30, 40)
    draw.rectangle([(0, HEADER_H),         (W, HEADER_H + 5)],   fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)],   fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def load_font(size):
        try:
            return ImageFont.truetype(FONT_BOLD, size)
        except Exception:
            return ImageFont.load_default()

    font_vs   = load_font(160)
    font_time = load_font(100)
    font_team = load_font(58)

    content_top = HEADER_H + 5
    content_bot = H - FOOTER_H - 5
    content_h   = content_bot - content_top

    logo_size     = 360
    name_h        = 120
    time_h        = 110
    gap_logo_name = 40
    gap_name_time = 60

    total_block_h = logo_size + gap_logo_name + name_h + gap_name_time + time_h
    block_top     = content_top + (content_h - total_block_h) // 2

    logo_y       = block_top
    name_block_y = logo_y + logo_size + gap_logo_name
    name_center  = name_block_y + name_h // 2
    time_y       = name_block_y + name_h + gap_name_time + time_h // 2

    # Logo trái
    if match.get("logo_a"):
        img = fetch_image(match["logo_a"])
        if img:
            img = img.resize((logo_size, logo_size), Image.LANCZOS)
            bg.paste(img, (W // 4 - logo_size // 2, logo_y), img)

    # Logo phải
    if match.get("logo_b"):
        img = fetch_image(match["logo_b"])
        if img:
            img = img.resize((logo_size, logo_size), Image.LANCZOS)
            bg.paste(img, (W * 3 // 4 - logo_size // 2, logo_y), img)

    draw.text((W // 2, logo_y + logo_size // 2), "VS",
              fill=ACCENT, font=font_vs, anchor="mm")

    def draw_team_name(text, cx):
        max_w     = W // 2 - 60
        font_size = 58
        f         = font_team
        while font_size >= 28:
            f    = load_font(font_size)
            bbox = draw.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_w:
                break
            font_size -= 3
        draw.text((cx, name_center), text, fill=(20, 20, 20), font=f, anchor="mm")

    if match.get("team_a"):
        draw_team_name(match["team_a"], W // 4)
    if match.get("team_b"):
        draw_team_name(match["team_b"], W * 3 // 4)

    if match.get("time"):
        draw.text((W // 2 + 4, time_y + 4), match["time"],
                  fill=ACCENT, font=font_time, anchor="mm")
        draw.text((W // 2, time_y), match["time"],
                  fill=(15, 15, 15), font=font_time, anchor="mm")

    # Tên giải — header
    if match.get("league"):
        league_text = match["league"].upper()
        font_size   = 62
        f           = None
        while font_size >= 28:
            f    = load_font(font_size)
            bbox = draw.textbbox((0, 0), league_text, font=f)
            if (bbox[2] - bbox[0]) <= W - 60:
                break
            font_size -= 3
        draw.text((W // 2, HEADER_H // 2), league_text,
                  fill=(255, 255, 255), font=f, anchor="mm")

    # BLV — footer
    if match.get("blv"):
        blv_text  = f"BLV: {match['blv']}"
        font_size = 58
        f         = None
        while font_size >= 28:
            f    = load_font(font_size)
            bbox = draw.textbbox((0, 0), blv_text, font=f)
            if (bbox[2] - bbox[0]) <= W - 60:
                break
            font_size -= 3
        draw.text((W // 2, H - FOOTER_H // 2), blv_text,
                  fill=(255, 255, 255), font=f, anchor="mm")

    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=(180, 180, 180), width=3)
    bg.save(out_path, "PNG", optimize=True)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHANNEL JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_channel(match: dict, thumb_url: str = "") -> dict:
    uid    = make_id(match["match_id"], "hqtv")
    src_id = make_id(match["match_id"], "src")
    ct_id  = make_id(match["match_id"], "ct")
    st_id  = make_id(match["match_id"], "st")

    stream_links = []
    for blv in match["blv_list"]:
        fhd_url  = blv.get("fhd", "")
        blv_name = blv.get("name", "")
        if not fhd_url:
            continue
        lnk_id = make_id(fhd_url + blv_name, "lnk")
        stream_links.append({
            "id":      lnk_id,
            "name":    blv_name if blv_name else "Link FHD",
            "type":    "hls",
            "default": len(stream_links) == 0,
            "url":     fhd_url,
            "request_headers": [
                {"key": "Referer",    "value": "https://hoiquantv.xyz/"},
                {"key": "User-Agent", "value": "Mozilla/5.0"},
            ],
        })

    label_text  = "● LIVE" if match["is_live"] else "🕐 Sắp"
    label_color = "#ff4444" if match["is_live"] else "#aaaaaa"

    display_name = match["name"]
    if match["time"]:
        display_name = f"{match['name']} | {match['time']}"

    channel = {
        "id":            uid,
        "name":          display_name,
        "type":          "single",
        "display":       "thumbnail-only",
        "enable_detail": False,
        "labels": [{"text": label_text, "position": "top-left",
                    "color": "#00000080", "text_color": label_color}],
        "sources": [{
            "id":   src_id,
            "name": "HoiQuanTV",
            "contents": [{
                "id":   ct_id,
                "name": match["name"],
                "streams": [{"id": st_id, "name": "KT", "stream_links": stream_links}],
            }],
        }],
        "org_metadata": {
            "league":    match.get("league",         ""),
            "team_a":    match.get("team_a",         ""),
            "team_b":    match.get("team_b",         ""),
            "logo_a":    match.get("logo_a",         ""),
            "logo_b":    match.get("logo_b",         ""),
            "time":      match.get("time",           ""),
            "blv":       match.get("blv",            ""),
            "is_live":   match["is_live"],
            "sport_slug": match.get("sport_slug",   ""),
        },
    }

    if thumb_url:
        channel["image"] = {
            "padding":          1,
            "background_color": "#ffffff",
            "display":          "contain",
            "url":              thumb_url,
            "width":            1600,
            "height":           1200,
        }

    return channel


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cleanup_old_thumbs(days=3)
    print(f"Giờ VN hiện tại: {now_vn().strftime('%H:%M %d/%m/%Y')}")

    matches = get_matches()

    live_count = sum(1 for m in matches if m["is_live"])
    print(f"Tổng: {len(matches)} | LIVE: {live_count} | Sắp: {len(matches) - live_count}\n")

    # Nhóm theo sport_slug, giữ thứ tự ưu tiên
    sport_channels: dict[str, list] = {}

    for i, match in enumerate(matches):
        sport_slug = match["sport_slug"]
        status     = "LIVE" if match["is_live"] else "SẮP"
        print(f"[{status} {i+1}/{len(matches)}] {match['name']} ({match['time']}) | BLV: {match['blv']}")

        # Build thumbnail
        uid        = make_id(match["match_id"], "hqtv")
        thumb_path = make_thumbnail(match, uid)
        cache_key  = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VERSION
        logo_hash  = hashlib.md5(cache_key.encode()).hexdigest()[:8]
        thumb_url  = f"{REPO_RAW}/{thumb_path}?v={logo_hash}" if REPO_RAW else ""

        channel = build_channel(match, thumb_url)

        if sport_slug not in sport_channels:
            sport_channels[sport_slug] = []
        sport_channels[sport_slug].append(channel)

        time.sleep(0.05)

    # Build groups
    # Lấy priority từ match đầu tiên của mỗi sport (đã được sort từ API)
    sport_priority: dict[str, int] = {}
    for m in matches:
        sl = m["sport_slug"]
        if sl not in sport_priority:
            sport_priority[sl] = m.get("sport_priority", 99)

    groups = []
    for sport_slug, channels in sport_channels.items():
        if not channels:
            continue
        emoji, display_name = SPORT_MAP.get(sport_slug, ("🏅", sport_slug.replace("-", " ").title()))

        live_cnt  = sum(1 for ch in channels if ch.get("org_metadata", {}).get("is_live", False))
        cate_name = f"{emoji} {display_name} ({live_cnt} LIVE)" if live_cnt > 0 else f"{emoji} {display_name}"

        groups.append({
            "id":            f"sport_{sport_slug}",
            "name":          cate_name,
            "display":       "vertical",
            "grid_number":   2,
            "enable_detail": False,
            "_priority":     sport_priority.get(sport_slug, 99),
            "channels":      channels,
        })

    # Bóng đá lên đầu, còn lại theo priority
    groups.sort(key=lambda g: (0 if g["id"] == "sport_bong-da" else 1, g["_priority"], g["name"]))
    for g in groups:
        g.pop("_priority", None)

    output = {
        "id":          "hoiquantv",
        "url":         "https://hoiquantv.xyz",
        "name":        "HoiQuanTV",
        "color":       "#1cb57a",
        "grid_number": 3,
        "image":       {"type": "cover", "url": "https://hoiquantv.xyz/favicon.ico"},
        "groups":      groups,
    }

    staging = "output_staging.json"
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(g["channels"]) for g in groups)

    def normalize(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            s = json.dumps(d, sort_keys=True, ensure_ascii=False)
            return re.sub(r"\?expire=\d+", "", s)
        except Exception:
            return ""

    old_norm = normalize("output.json")
    new_norm = normalize(staging)

    if old_norm != new_norm:
        os.replace(staging, "output.json")
        print(f"\nXong! {total} kênh, {len(groups)} môn thể thao → output.json (ĐÃ CẬP NHẬT)")
    else:
        os.remove(staging)
        print(f"\nXong! {total} kênh, {len(groups)} môn thể thao → Không có thay đổi, giữ nguyên output.json")


if __name__ == "__main__":
    main()
