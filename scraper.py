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
# TIMEZONE & IS_LIVE — fix theo giờ VN thực
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ       = timezone(timedelta(hours=7))
LIVE_BEFORE = timedelta(minutes=15)


def now_vn() -> datetime:
    return datetime.now(tz=VN_TZ)


def parse_kickoff(time_str: str):
    if not time_str or not time_str.strip():
        return None
    s     = time_str.strip()
    today = now_vn()
    year  = today.year

    patterns = [
        (r"(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})/(\d{4})",
         lambda m: datetime(int(m[4]), int(m[3]), int(m[2]), int(m[0]), int(m[1]), tzinfo=VN_TZ)),
        (r"(\d{1,2}):(\d{2})\s+(\d{1,2})/(\d{1,2})$",
         lambda m: datetime(year,    int(m[3]), int(m[2]), int(m[0]), int(m[1]), tzinfo=VN_TZ)),
        (r"^(\d{1,2}):(\d{2})$",
         lambda m: datetime(today.year, today.month, today.day, int(m[0]), int(m[1]), tzinfo=VN_TZ)),
    ]
    for pattern, builder in patterns:
        match = re.search(pattern, s)
        if match:
            try:
                return builder(match.groups())
            except ValueError:
                pass
    return None


def calc_is_live(api_live: bool, time_str: str) -> bool:
    if api_live:
        return True
    kickoff = parse_kickoff(time_str)
    if kickoff is None:
        return False
    now = now_vn()
    return now >= (kickoff - LIVE_BEFORE)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://sv2.hoiquan3.live/",
}

API_BASE    = "https://sv.hoiquantv.xyz/api/v1/external/fixtures"
THUMBS_DIR  = "thumbs"
REPO_RAW    = os.environ.get("REPO_RAW", "")

CATE_MAP = {
    "bong-da":     "⚽ Bóng Đá",
    "tennis":      "🎾 Tennis",
    "bong-ban":    "🏓 Bóng Bàn",
    "billiards":   "🎱 Billiards",
    "esports":     "🎮 Esports",
    "bong-chuyen": "🏐 Bóng Chuyền",
    "cau-long":    "🏸 Cầu Lông",
    "bong-ro":     "🏀 Bóng Rổ",
    "dua-xe":      "🏎️ Đua Xe",
    "boxing":      "🥊 Boxing",
}

EXCLUDE_LEAGUES_AMERICA = [
    "colombian", "colombia", "liga betplay", "categoria primera", "primera a",
    "chile", "primera division chile",
    "ecuador", "liga pro ecuador",
    "peru", "liga 1 peru", "liga 1 perú",
    "venezuela", "liga futve",
    "paraguay", "apertura paraguay",
    "uruguay", "primera division uruguay",
    "bolivia", "division profesional",
]

THUMB_VERSION = "v2"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def is_america_league(league_name: str) -> bool:
    lower = league_name.lower()
    return any(kw in lower for kw in EXCLUDE_LEAGUES_AMERICA)


def make_id(text, prefix):
    h = hashlib.md5(text.encode()).hexdigest()[:10]
    return f"{prefix}-{h}"


def fetch_image(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except:
        return None


def parse_time_sort(match_time: str) -> int:
    kickoff = parse_kickoff(match_time)
    if kickoff:
        return kickoff.month * 10_000_000 + kickoff.day * 10_000 + kickoff.hour * 100 + kickoff.minute
    return 999_999_999


def is_within_24h(match_time: str, sport_slug: str = "bong-da") -> bool:
    if sport_slug != "bong-da":
        return True
    kickoff = parse_kickoff(match_time)
    if kickoff is None:
        return True
    now   = now_vn()
    lower = now - timedelta(hours=6)
    upper = now + timedelta(hours=24)
    return lower <= kickoff <= upper


def utc_to_vn_str(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        vn = dt.astimezone(VN_TZ)
        return vn.strftime("%H:%M %d/%m")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────────────────────

def make_thumbnail(match, blv_names_list, match_key):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cache_key = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VERSION
    logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    date_str  = now_vn().strftime("%Y%m%d")
    
    # Dùng match_key thay vì channel_id để đảm bảo cùng 1 trận = 1 file ảnh
    # Thay thế dấu ":" để tránh lỗi tạo file trên Linux
    safe_key  = match_key.replace(":", "-")
    out_path  = f"{THUMBS_DIR}/{safe_key}_{logo_hash}_{date_str}.png"

    if os.path.exists(out_path):
        return out_path

    W, H = 1600, 1200
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
    draw.rectangle([(0, HEADER_H),         (W, HEADER_H + 5)],    fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)],    fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font_vs   = ImageFont.truetype(FONT_BOLD, 160)
        font_time = ImageFont.truetype(FONT_BOLD, 100)
        font_team = ImageFont.truetype(FONT_BOLD, 58)
        font_blv  = ImageFont.truetype(FONT_BOLD, 58)
    except:
        font_vs = font_time = font_team = font_blv = ImageFont.load_default()

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

    if match.get("logo_a"):
        img = fetch_image(match["logo_a"])
        if img:
            img = img.resize((logo_size, logo_size), Image.LANCZOS)
            x   = W // 4 - logo_size // 2
            bg.paste(img, (x, logo_y), img)

    if match.get("logo_b"):
        img = fetch_image(match["logo_b"])
        if img:
            img = img.resize((logo_size, logo_size), Image.LANCZOS)
            x   = W * 3 // 4 - logo_size // 2
            bg.paste(img, (x, logo_y), img)

    draw.text((W // 2, logo_y + logo_size // 2), "VS", fill=ACCENT, font=font_vs, anchor="mm")

    def draw_team_name(text, cx):
        max_width = W // 2 - 60
        font_size = 58
        f         = font_team
        while font_size >= 28:
            try:
                f = ImageFont.truetype(FONT_BOLD, font_size)
            except:
                f = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_width:
                break
            font_size -= 3
        draw.text((cx, name_center), text, fill=(20, 20, 20), font=f, anchor="mm")

    if match.get("team_a"): draw_team_name(match["team_a"], W // 4)
    if match.get("team_b"): draw_team_name(match["team_b"], W * 3 // 4)

    if match.get("time"):
        draw.text((W // 2 + 4, time_y + 4), match["time"], fill=ACCENT, font=font_time, anchor="mm")
        draw.text((W // 2, time_y), match["time"], fill=(15, 15, 15), font=font_time, anchor="mm")

    if match.get("league"):
        league_text = match["league"].upper()
        font_size   = 62
        f           = None
        while font_size >= 28:
            try:
                f = ImageFont.truetype(FONT_BOLD, font_size)
            except:
                f = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), league_text, font=f)
            if (bbox[2] - bbox[0]) <= W - 60:
                break
            font_size -= 3
        draw.text((W // 2, HEADER_H // 2), league_text, fill=(255, 255, 255), font=f, anchor="mm")

    # Vẽ BLV từ danh sách đã lọc trùng
    if blv_names_list:
        blv_text  = "BLV: " + ", ".join(blv_names_list)
        font_size = 58
        f         = None
        while font_size >= 28:
            try:
                f = ImageFont.truetype(FONT_BOLD, font_size)
            except:
                f = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), blv_text, font=f)
            if (bbox[2] - bbox[0]) <= W - 60:
                break
            font_size -= 3
        draw.text((W // 2, H - FOOTER_H // 2), blv_text, fill=(255, 255, 255), font=f, anchor="mm")

    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=(180, 180, 180), width=3)
    bg.save(out_path, "PNG", optimize=True)
    return out_path

def cleanup_old_thumbs(days: int = 3):
    if not os.path.exists(THUMBS_DIR):
        return
    cutoff = now_vn() - timedelta(days=days)
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
                print(f"  Loi xoa thumb {fname}: {e}")
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
                print(f"  Loi xoa thumb {fname}: {e}")
    if removed:
        print(f"Da xoa {removed} thumbnail cu (>{days} ngay)")


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPE & GROUP MATCHES
# ─────────────────────────────────────────────────────────────────────────────

def get_grouped_matches():
    """Lấy danh sách trận từ API, gom nhóm theo Trận Đấu (dù khác ID)."""
    try:
        res_unfinished = requests.get(f"{API_BASE}/unfinished", headers=HEADERS, timeout=15)
        res_unfinished.raise_for_status()
        data_unfinished = res_unfinished.json()
    except Exception as e:
        print(f"Loi API unfinished: {e}")
        data_unfinished = []

    finished_ids = set()
    try:
        res_finished = requests.get(f"{API_BASE}/finished", headers=HEADERS, timeout=15)
        res_finished.raise_for_status()
        data_finished = res_finished.json()
        for item in data_finished.get("data", []) if isinstance(data_finished, dict) else []:
            if isinstance(item, dict) and item.get("id"):
                finished_ids.add(str(item["id"]))
    except Exception:
        pass

    fixtures = data_unfinished.get("data", []) if isinstance(data_unfinished, dict) else []
    
    # Dict lưu trữ các trận đã gom: key = "teamA_teamB_time", value = data trận
    grouped_matches = {}

    for fix in fixtures:
        if not isinstance(fix, dict):
            continue

        match_id = str(fix.get("id", ""))
        if not match_id or match_id in finished_ids:
            continue

        sport = fix.get("sport", {}) or {}
        sport_slug = sport.get("slug", "bong-da")
        sport_priority = sport.get("priority", 999)

        league = fix.get("league", {}) or {}
        league_name = league.get("name", "")

        home = fix.get("homeTeam", {}) or {}
        away = fix.get("awayTeam", {}) or {}
        team_a = home.get("name", "").strip()
        team_b = away.get("name", "").strip()
        logo_a = home.get("logoUrl", "")
        logo_b = away.get("logoUrl", "")

        if not team_a or not team_b:
            continue

        if sport_slug == "bong-da" and is_america_league(league_name):
            continue

        start_time = fix.get("startTime", "")
        match_time = utc_to_vn_str(start_time)

        if not is_within_24h(match_time, sport_slug):
            continue

        api_live = fix.get("isLive", False)
        is_live_flag = calc_is_live(api_live, match_time)

        # Lấy BLV & Link FHD
        commentators_raw = fix.get("fixtureCommentators", [])
        
        for comm in commentators_raw:
            if not isinstance(comm, dict):
                continue
            comm_obj = comm.get("commentator", {}) or {}
            comm_name = comm_obj.get("name", "").strip()
            streams_list = comm_obj.get("streams", []) or []
            fhd_url = ""
            if isinstance(streams_list, list):
                for stream in streams_list:
                    if isinstance(stream, dict) and stream.get("name") == "FHD":
                        fhd_url = stream.get("sourceUrl", "")
                        break
            
            if not comm_name or not fhd_url:
                continue

            # Tạo Match Key để gom nhóm
            match_key = f"{team_a}_{team_b}_{match_time}"
            
            # Nếu chưa có key này, khởi tạo dữ liệu trận
            if match_key not in grouped_matches:
                grouped_matches[match_key] = {
                    "sport_slug":     sport_slug,
                    "sport_priority": sport_priority,
                    "name":           f"{team_a} vs {team_b}",
                    "time":           match_time,
                    "time_sort":      parse_time_sort(match_time),
                    "team_a":         team_a,
                    "team_b":         team_b,
                    "logo_a":         logo_a,
                    "logo_b":         logo_b,
                    "league":         league_name,
                    "is_live":        False,
                    "blvs_dict": {},  # {"BLV A": ["url1", "url2"], "BLV B": ["url3"]}
                }

            g_match = grouped_matches[match_key]
            
            # Cập nhật lại logo nếu fixture khác có logo rõ nét hơn (nếu bị rỗng)
            if not g_match["logo_a"] and logo_a: g_match["logo_a"] = logo_a
            if not g_match["logo_b"] and logo_b: g_match["logo_b"] = logo_b
            
            # Nếu 1 trong các fixture là Live thì cả trận tính là Live
            if is_live_flag:
                g_match["is_live"] = True

            # Thêm URL vào dict của BLV tương ứng (tự động gộp nếu trùng tên BLV)
            if comm_name not in g_match["blvs_dict"]:
                g_match["blvs_dict"][comm_name] = []
            
            if fhd_url not in g_match["blvs_dict"][comm_name]:
                g_match["blvs_dict"][comm_name].append(fhd_url)

    return grouped_matches


# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHANNEL JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_channel(match, match_key, thumb_url=""):
    uid    = make_id(match_key, "hqlive")
    src_id = make_id(match_key, "src")
    ct_id  = make_id(match_key, "ct")
    st_id  = make_id(match_key, "st")

    stream_links = []
    
    # Chỉ tạo stream_links nếu trận đang Live
    if match["is_live"]:
        for blv_name, urls in match["blvs_dict"].items():
            for idx, s_url in enumerate(urls):
                # Case 1: Cùng BLV có nhiều link -> Tên "BLV A 1", "BLV A 2"
                # Case 2: Chỉ 1 link -> Tên "BLV A"
                name = f"{blv_name} {idx + 1}" if len(urls) > 1 else blv_name
                
                lnk_id = make_id(s_url + str(idx), "lnk")
                stream_links.append({
                    "id":      lnk_id,
                    "name":    name,
                    "type":    "hls",
                    "default": len(stream_links) == 0,
                    "url":     s_url,
                    "request_headers": [
                        {"key": "Referer",    "value": "https://sv2.hoiquan3.live/"},
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
                "streams": [{"id": st_id, "name": "HQ", "stream_links": stream_links}],
            }],
        }],
        "org_metadata": {
            "league":     match.get("league",      ""),
            "team_a":     match.get("team_a",      ""),
            "team_b":     match.get("team_b",      ""),
            "logo_a":     match.get("logo_a",      ""),
            "logo_b":     match.get("logo_b",      ""),
            "time":       match.get("time",        ""),
            "blv":        ", ".join(match.get("blvs_dict", {}).keys()),
            "is_live":    match["is_live"],
            "sport_slug": match.get("sport_slug",  ""),
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
    print("\n=============================")
    print("      V3.0 - Grouping Logic")
    print("=============================\n")
    
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cleanup_old_thumbs(days=3)
    print(f"Gio VN hien tai : {now_vn().strftime('%H:%M %d/%m/%Y')}")
    print("Lay & gom nhom tran dau tu hoiquan3.live...")
    
    grouped_matches = get_grouped_matches()
    
    # Chuyển Dict sang List để sort
    matches_list = list(grouped_matches.values())
    
    live_count = sum(1 for m in matches_list if m["is_live"])
    print(f"Tong: {len(matches_list)} | LIVE: {live_count} | Sap: {len(matches_list)-live_count}\n")

    # Sort: Live trước, theo priority môn, theo giờ
    matches_list.sort(key=lambda m: (0 if m["is_live"] else 1, m["sport_priority"], m["time_sort"]))

    sport_channels = {}

    for i, match in enumerate(matches_list):
        sport_slug = match["sport_slug"]
        match_key  = f"{match['team_a']}_{match['team_b']}_{match['time']}"
        
        status  = "LIVE" if match["is_live"] else "SAP"
        blv_str = ", ".join(match["blvs_dict"].keys())
        print(f"[{status} {i+1}/{len(matches_list)}] {match['name']} ({match['time']}) | BLV: {blv_str}")

        # Tạo Thumbnail (truyền vào list key BLV đã loại trùng)
        blv_names_list = list(match["blvs_dict"].keys())
        thumb_path = make_thumbnail(match, blv_names_list, match_key)
        
        cache_key = match.get("logo_a", "") + match.get("logo_b", "") + THUMB_VERSION
        logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
        thumb_url  = f"{REPO_RAW}/{thumb_path}?v={logo_hash}" if REPO_RAW else ""

        channel = build_channel(match, match_key, thumb_url)

        if sport_slug not in sport_channels:
            sport_channels[sport_slug] = []
        sport_channels[sport_slug].append(channel)

        time.sleep(0.1) # Giảm xuống 0.1 vì tạo thumb đã được cache tốt hơn

    cate_channels = {slug: [] for slug in CATE_MAP}
    for slug, channels in sport_channels.items():
        if slug not in cate_channels:
            cate_channels[slug] = []
        cate_channels[slug].extend(channels)

    slug_priority = {}
    for m in matches_list:
        slug = m["sport_slug"]
        if slug not in slug_priority:
            slug_priority[slug] = m["sport_priority"]

    ordered_slugs = sorted(
        cate_channels.keys(),
        key=lambda s: (0 if s == "bong-da" else 1, slug_priority.get(s, 999))
    )

    groups = []
    for sport_slug in ordered_slugs:
        channels = cate_channels[sport_slug]
        if not channels:
            continue
        cate_info = CATE_MAP.get(sport_slug, f"🏅 {sport_slug}")

        live_count = sum(1 for ch in channels if ch.get("org_metadata", {}).get("is_live", False))
        cate_name  = f"{cate_info} ({live_count} LIVE)" if live_count > 0 else cate_info

        groups.append({
            "id":            f"sport_{sport_slug}",
            "name":          cate_name,
            "display":       "vertical",
            "grid_number":   2,
            "enable_detail": False,
            "channels":      channels,
        })

    updated_at_str = now_vn().strftime("%H:%M %d/%m/%Y")

    output = {
        "id":          "hoiquan",
        "url":         "https://sv2.hoiquan3.live",
        "name":        "HoiQuanTV",
        "color":       "#1cb57a",
        "updated_at":  updated_at_str,
        "grid_number": 3,
        "image":       {"type": "cover", "url": "https://sv2.hoiquan3.live/logo.png"},
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
            s = re.sub(r'"updated_at":\s*"[^"]*"', '"updated_at": "__DYNAMIC__"', s)
            return re.sub(r"\?expire=\d+", "", s)
        except Exception:
            return ""

    old_norm = normalize("output.json")
    new_norm = normalize(staging)

    if old_norm != new_norm:
        os.replace(staging, "output.json")
        print(f"\nXong! {total} kenh, {len(groups)} mon the thao -> output.json (DA CAP NHAT)")
    else:
        os.remove(staging)
        print(f"\nXong! {total} kenh, {len(groups)} mon the thao -> Khong co thay doi, giu nguyen output.json")


if __name__ == "__main__":
    main()
