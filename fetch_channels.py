import json
import os
import re
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MAX_RESULTS_PER_QUERY = 20
MAX_NEW_VIDEOS_PER_QUERY = 10
MIN_DURATION_SECONDS = 60   # skip sub-1-min clips
NEW_VIDEOS_DAYS = 365       # fetch videos published within this many days

OWN_CHANNEL_NAME = "ข้อสอบ IC Investment License"

SEARCH_QUERIES = [
    "สรุปสอบ IC",
    "ข้อสอบ IC",
    "ติวสอบ IC",
    "IC Plain สอบ",
    "สอบผ่าน IC",
]

# Keywords for new-video alerts — sorted by date, not relevance
NEW_VIDEO_QUERIES = [
    "สรุปสอบ IC",
    "ข้อสอบ IC",
    "ติวสอบ IC",
    "IC Plain สอบ",
    "สอบผ่าน IC",
]

BASE_URL = "https://www.googleapis.com/youtube/v3"

IC_KEYWORDS = [
    "ติว", "สรุป", "ผ่าน", "ครั้งเดียว", "เทคนิค", "เคล็ดลับ", "แนวข้อสอบ",
    "หมวด", "plain", "complex", "EP", "part", "license", "IC", "IP",
]

COMMENTS_PER_VIDEO = 30
TOP_VIDEOS_FOR_COMMENTS = 15
SIX_MONTHS_SECONDS = 180 * 24 * 3600


def search_videos(query: str) -> list[str]:
    resp = requests.get(f"{BASE_URL}/search", params={
        "key": API_KEY,
        "q": query,
        "type": "video",
        "part": "id",
        "maxResults": MAX_RESULTS_PER_QUERY,
        "order": "relevance",
        "regionCode": "TH",
        "relevanceLanguage": "th",
    })
    resp.raise_for_status()
    return [item["id"]["videoId"] for item in resp.json().get("items", [])]


def search_new_videos(query: str, published_after: str) -> list[str]:
    """Search sorted by date, after a given RFC 3339 timestamp."""
    resp = requests.get(f"{BASE_URL}/search", params={
        "key": API_KEY,
        "q": query,
        "type": "video",
        "part": "id",
        "maxResults": MAX_NEW_VIDEOS_PER_QUERY,
        "order": "date",
        "publishedAfter": published_after,
        "regionCode": "TH",
        "relevanceLanguage": "th",
    })
    resp.raise_for_status()
    return [item["id"]["videoId"] for item in resp.json().get("items", [])]


def has_thai(text: str) -> bool:
    return bool(re.search(r'[฀-๿]', text))


def parse_duration(iso_duration: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return 0
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


def get_video_details(video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    resp = requests.get(f"{BASE_URL}/videos", params={
        "key": API_KEY,
        "id": ",".join(video_ids),
        "part": "snippet,statistics,contentDetails",
    })
    resp.raise_for_status()
    videos = []
    for item in resp.json().get("items", []):
        duration_s = parse_duration(item["contentDetails"].get("duration", ""))
        if duration_s < MIN_DURATION_SECONDS:
            continue
        snippet = item["snippet"]
        title = snippet.get("title", "")
        if not has_thai(title):
            continue  # skip non-Thai content
        stats   = item.get("statistics", {})
        thumbs  = snippet.get("thumbnails", {})
        thumb_url = (
            thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or {}
        ).get("url", "")
        view_count    = int(stats.get("viewCount",    0))
        like_count    = int(stats.get("likeCount",    0))
        comment_count = int(stats.get("commentCount", 0))
        engagement_rate = round(
            (like_count + comment_count) / view_count * 100, 2
        ) if view_count > 0 else 0.0
        videos.append({
            "video_id":        item["id"],
            "title":           snippet.get("title", ""),
            "thumbnail_url":   thumb_url,
            "duration_seconds": duration_s,
            "view_count":      view_count,
            "like_count":      like_count,
            "comment_count":   comment_count,
            "engagement_rate": engagement_rate,
            "publish_date":    snippet.get("publishedAt", ""),
            "channel_id":      snippet.get("channelId", ""),
            "channel_name":    snippet.get("channelTitle", ""),
        })
    return videos


# ── Insights ──────────────────────────────────────────────────────────────────

def analyze_competitors(channels: list[dict]) -> list[dict]:
    competitor_videos = [
        v for ch in channels
        if not ch.get("is_own_channel")
        for v in ch.get("videos", [])
    ]
    own_videos = [
        v for ch in channels
        if ch.get("is_own_channel")
        for v in ch.get("videos", [])
    ]

    if not competitor_videos:
        return []

    insights = []

    # 1. Top title keywords by avg view count
    keyword_views: dict[str, list[int]] = {}
    for v in competitor_videos:
        title_lower = v["title"].lower()
        for kw in IC_KEYWORDS:
            if kw.lower() in title_lower:
                keyword_views.setdefault(kw, []).append(v["view_count"])
    if keyword_views:
        kw_avg = {kw: sum(vs) / len(vs) for kw, vs in keyword_views.items() if len(vs) >= 2}
        if kw_avg:
            top_kw = max(kw_avg, key=lambda k: kw_avg[k])
            overall_avg = sum(v["view_count"] for v in competitor_videos) / len(competitor_videos)
            multiplier = round(kw_avg[top_kw] / overall_avg, 1) if overall_avg > 0 else 0
            insights.append({
                "type": "title_pattern",
                "icon": "🏆",
                "tip": f"Videos with \"{top_kw}\" in the title average {multiplier}x more views than the niche average",
                "evidence": f"Avg views with keyword: {int(kw_avg[top_kw]):,} vs. overall avg: {int(overall_avg):,}",
            })

    # 2. Best day to post
    day_views: dict[str, list[int]] = {}
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for v in competitor_videos:
        try:
            dt = datetime.fromisoformat(v["publish_date"].replace("Z", "+00:00"))
            day = day_names[dt.weekday()]
            day_views.setdefault(day, []).append(v["view_count"])
        except Exception:
            pass
    if day_views:
        best_day = max(day_views, key=lambda d: sum(day_views[d]) / len(day_views[d]))
        best_day_avg = int(sum(day_views[best_day]) / len(day_views[best_day]))
        insights.append({
            "type": "timing",
            "icon": "📅",
            "tip": f"Videos posted on {best_day} average the highest views in this niche",
            "evidence": f"Avg views on {best_day}: {best_day_avg:,} (based on {len(day_views[best_day])} videos)",
        })

    # 3. Engagement leader
    ch_engagement = {}
    for ch in channels:
        if ch.get("is_own_channel") or not ch.get("videos"):
            continue
        avg_eng = sum(v["engagement_rate"] for v in ch["videos"]) / len(ch["videos"])
        ch_engagement[ch["channel_name"]] = round(avg_eng, 2)
    if ch_engagement:
        leader = max(ch_engagement, key=lambda c: ch_engagement[c])
        insights.append({
            "type": "engagement",
            "icon": "🔥",
            "tip": f"{leader} drives the most audience interaction — study their format and CTA style",
            "evidence": f"Avg engagement rate: {ch_engagement[leader]}% (likes + comments ÷ views)",
        })

    # 4. Content gap
    sorted_comp = sorted(competitor_videos, key=lambda v: v["view_count"], reverse=True)
    top_comp_titles = " ".join(v["title"].lower() for v in sorted_comp[:10])
    own_titles = " ".join(v["title"].lower() for v in own_videos)
    gap_keywords = [kw for kw in IC_KEYWORDS if kw.lower() in top_comp_titles and kw.lower() not in own_titles]
    if gap_keywords:
        insights.append({
            "type": "content_gap",
            "icon": "🎯",
            "tip": f"Top search results use \"{gap_keywords[0]}\" frequently — your recent videos don't",
            "evidence": f"Missing keywords from your titles: {', '.join(gap_keywords[:4])}",
        })

    # 5. Optimal video length
    sorted_by_views = sorted(competitor_videos, key=lambda v: v["view_count"], reverse=True)
    top_bucket = sorted_by_views[:max(1, len(sorted_by_views) // 5)]
    bottom_bucket = sorted_by_views[len(sorted_by_views) // 2:]
    avg_top_dur = sum(v["duration_seconds"] for v in top_bucket) / len(top_bucket)
    avg_bot_dur = sum(v["duration_seconds"] for v in bottom_bucket) / len(bottom_bucket)

    def fmt_min(s): return f"{int(s // 60)}m"

    insights.append({
        "type": "video_length",
        "icon": "⏱",
        "tip": f"Top-performing videos average {fmt_min(avg_top_dur)} — {'longer' if avg_top_dur > avg_bot_dur else 'shorter'} than lower-performing ones ({fmt_min(avg_bot_dur)})",
        "evidence": f"Top 20% avg length: {fmt_min(avg_top_dur)} | Bottom 50% avg length: {fmt_min(avg_bot_dur)}",
    })

    return insights


# ── Comment mining ────────────────────────────────────────────────────────────

def fetch_audience_questions(channels: list[dict]) -> list[dict]:
    """Pull recent comments (< 6 months) from top videos across all channels."""
    now = datetime.now(timezone.utc)

    all_videos = [
        (v, ch["channel_name"])
        for ch in channels
        if not ch.get("is_own_channel")
        for v in ch.get("videos", [])
    ]
    top_videos = sorted(all_videos, key=lambda x: x[0]["view_count"], reverse=True)[:TOP_VIDEOS_FOR_COMMENTS]

    questions = []
    for v, ch_name in top_videos:
        try:
            resp = requests.get(f"{BASE_URL}/commentThreads", params={
                "key": API_KEY,
                "videoId": v["video_id"],
                "part": "snippet",
                "order": "relevance",
                "maxResults": COMMENTS_PER_VIDEO,
            })
            if resp.status_code == 403:
                continue
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                c = item["snippet"]["topLevelComment"]["snippet"]
                published_at = c.get("publishedAt", "")
                try:
                    comment_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    age_seconds = (now - comment_dt).total_seconds()
                    if age_seconds > SIX_MONTHS_SECONDS:
                        continue
                except Exception:
                    continue

                text = c.get("textDisplay", "").strip()
                likes = c.get("likeCount", 0)
                is_question = any(kw in text for kw in ["?", "ไหม", "อย่างไร", "ยังไง", "ที่ไหน", "เท่าไร", "กี่"])
                questions.append({
                    "text":        text,
                    "likes":       likes,
                    "is_question": is_question,
                    "published_at": published_at,
                    "age_days":    int(age_seconds / 86400),
                    "video_id":    v["video_id"],
                    "video_title": v["title"],
                    "channel_name": ch_name,
                    "video_url":   f"https://www.youtube.com/watch?v={v['video_id']}",
                })
        except Exception as e:
            print(f"  [warn] comments failed for {v['video_id']}: {e}")

    questions.sort(key=lambda c: (not c["is_question"], c["age_days"], -c["likes"]))
    return questions[:80]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)

    # 1. Relevance search (Videos tab)
    seen_ids: set[str] = set()
    all_video_ids: list[str] = []
    for query in SEARCH_QUERIES:
        print(f"Searching: {query}")
        ids = search_videos(query)
        new_ids = [vid for vid in ids if vid not in seen_ids]
        seen_ids.update(new_ids)
        all_video_ids.extend(new_ids)
        print(f"  -> {len(new_ids)} new videos (total {len(all_video_ids)})")

    print(f"\nFetching details for {len(all_video_ids)} unique videos...")
    raw_videos: list[dict] = []
    for i in range(0, len(all_video_ids), 50):
        raw_videos.extend(get_video_details(all_video_ids[i:i + 50]))
    print(f"  -> {len(raw_videos)} videos after duration filter")

    # 2. Group by channel
    channels_map: dict[str, dict] = {}
    for v in raw_videos:
        ch_name = v.pop("channel_name")
        ch_id   = v.pop("channel_id")
        if ch_name not in channels_map:
            channels_map[ch_name] = {
                "channel_name":   ch_name,
                "channel_id":     ch_id,
                "is_own_channel": ch_name == OWN_CHANNEL_NAME,
                "videos":         [],
            }
        channels_map[ch_name]["videos"].append(v)

    channels = sorted(
        channels_map.values(),
        key=lambda c: sum(v["view_count"] for v in c["videos"]),
        reverse=True,
    )
    print(f"  -> {len(channels)} unique channels")

    # 3. New videos search (date-sorted, last N days)
    cutoff = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat().replace("+00:00", "Z")
    from datetime import timedelta
    published_after = (now - timedelta(days=NEW_VIDEOS_DAYS)).strftime("%Y-%m-%dT00:00:00Z")

    print(f"\nSearching new videos (last {NEW_VIDEOS_DAYS} days)...")
    new_seen: set[str] = set()
    new_video_ids: list[str] = []
    for query in NEW_VIDEO_QUERIES:
        ids = search_new_videos(query, published_after)
        fresh = [vid for vid in ids if vid not in new_seen and vid not in seen_ids]
        new_seen.update(fresh)
        new_video_ids.extend(fresh)
        print(f"  [{query}] -> {len(fresh)} new")

    new_raw: list[dict] = []
    for i in range(0, len(new_video_ids), 50):
        new_raw.extend(get_video_details(new_video_ids[i:i + 50]))

    # Flatten channel_name/channel_id back into each video for the new_videos list
    new_videos_flat = []
    for v in new_raw:
        new_videos_flat.append({
            **v,
            "video_url": f"https://www.youtube.com/watch?v={v['video_id']}",
        })
    new_videos_flat.sort(key=lambda v: v["publish_date"], reverse=True)
    print(f"  -> {len(new_videos_flat)} new videos total")

    # 4. Insights + comments
    insights = analyze_competitors(channels)
    print(f"\nMining comments from top {TOP_VIDEOS_FOR_COMMENTS} videos...")
    audience_questions = fetch_audience_questions(channels)
    print(f"  -> {len(audience_questions)} comments ({sum(1 for q in audience_questions if q['is_question'])} questions)")

    output = {
        "fetched_at":         now.isoformat(),
        "search_queries":     SEARCH_QUERIES,
        "new_video_queries":  NEW_VIDEO_QUERIES,
        "new_videos_days":    NEW_VIDEOS_DAYS,
        "channels":           channels,
        "new_videos":         new_videos_flat,
        "insights":           insights,
        "audience_questions": audience_questions,
    }

    out_path = "channels_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved — {len(channels)} channels, {len(raw_videos)} videos, {len(new_videos_flat)} new videos, {len(audience_questions)} comments")


if __name__ == "__main__":
    main()
