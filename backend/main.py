"""
Down4U Backend - Universal Multi-Platform Video Downloader Engine
Supports: TikTok, YouTube, Instagram, Twitter/X, Facebook, and Web
"""

import os, re, json, asyncio, logging, tempfile, urllib.parse
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

import httpx
import yt_dlp
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("down4u")

COOKIES_ENV = "YT_COOKIES"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)

# ─── Cookie File — written ONCE at startup, cached for all requests ──────────
_COOKIE_FILE_PATH: Optional[str] = None

def _init_cookie_file() -> Optional[str]:
    """Write cookie file once at startup (merging YouTube and Instagram cookies) and cache path."""
    global _COOKIE_FILE_PATH
    if _COOKIE_FILE_PATH:
        return _COOKIE_FILE_PATH

    raw_yt = os.environ.get(COOKIES_ENV, "").strip() or os.environ.get("COOKIES_TXT", "").strip()
    raw_ig = os.environ.get("IG_COOKIES", "").strip()
    session_id = os.environ.get("INSTAGRAM_SESSION_ID", "").strip()

    if not raw_yt and not raw_ig and not session_id:
        return None

    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("# Netscape HTTP Cookie File\n")
        if raw_yt:
            # Strip redundant header if present
            yt_clean = raw_yt.replace("# Netscape HTTP Cookie File", "").strip()
            tmp.write(yt_clean + "\n")
        if raw_ig:
            ig_clean = raw_ig.replace("# Netscape HTTP Cookie File", "").strip()
            tmp.write(ig_clean + "\n")
        if session_id:
            # Synthesize valid Instagram session cookies
            tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{session_id}\n")
            tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tds_user_id\t0\n")
            tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tcsrftoken\tmissing_csrf\n")

        tmp.flush()
        tmp.close()
        _COOKIE_FILE_PATH = tmp.name
        logger.info("Cookie file written and cached: %s (IG session: %s)", tmp.name, bool(session_id or raw_ig))
        return tmp.name
    except Exception as e:
        logger.warning("Failed to write cookie file: %s", e)
        return None

# Keep backward-compat alias
def _write_cookie_file() -> Optional[str]:
    return _init_cookie_file()


# ─── Platform Detection ──────────────────────────────────────────────────────
def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "twitter.com" in u or "x.com" in u or "t.co" in u:
        return "Twitter"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "facebook.com" in u or "fb.watch" in u:
        return "Facebook"
    if "reddit.com" in u or "redd.it" in u:
        return "Reddit"
    return "Web"


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return ""
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def _sanitize_title(title: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\- ]", "", title).strip().replace(" ", "_")
    return s[:max_len] if s else "video"


# ─── TikTok Engine (TikWM API) ───────────────────────────────────────────────
async def extract_tiktok(url: str) -> Optional[Dict[str, Any]]:
    """Ultra-fast, 100% free, no-watermark TikTok extraction via TikWM API."""
    try:
        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            target_tiktok_url = url
            # Resolve short URLs if needed (vm.tiktok.com, vt.tiktok.com)
            if "vm.tiktok.com" in url or "vt.tiktok.com" in url or "m.tiktok.com" in url:
                try:
                    head_res = await client.head(url)
                    if head_res.url:
                        target_tiktok_url = str(head_res.url)
                except Exception as exp_err:
                    logger.debug("TikTok short URL expand error: %s", exp_err)

            endpoints = ["https://www.tikwm.com/api/", "https://tikwm.com/api/"]
            for ep in endpoints:
                try:
                    res = await client.post(ep, data={"url": target_tiktok_url}, headers=headers)
                    if res.status_code == 200:
                        d = res.json()
                        if d.get("code") == 0 and d.get("data"):
                            data = d["data"]
                            title = data.get("title") or "TikTok Video"
                            direct_mp4 = data.get("play") or data.get("wmplay")
                            cover = data.get("cover") or data.get("origin_cover")
                            duration_sec = data.get("duration", 0)
                            size_bytes = data.get("size", 0)
                            size_str = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes else "HD MP4"
                            safe_name = f"{_sanitize_title(title)}_TikTok.mp4"

                            encoded_url = urllib.parse.quote(url, safe="")
                            encoded_stream = urllib.parse.quote(direct_mp4, safe="")

                            qualities = [
                                {
                                    "label": "HD No Watermark",
                                    "height": 720,
                                    "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                                    "direct_url": direct_mp4
                                }
                            ]
                            if data.get("wmplay"):
                                qualities.append({
                                    "label": "Standard (Watermark)",
                                    "height": 540,
                                    "downloadUrl": f"/api/download?url={encoded_url}&direct_url={urllib.parse.quote(data['wmplay'], safe='')}&filename={urllib.parse.quote(safe_name, safe='')}",
                                    "direct_url": data["wmplay"]
                                })

                            return {
                                "success": True,
                                "status": "success",
                                "platform": "TikTok",
                                "title": title,
                                "thumbnail": cover,
                                "duration": _format_duration(duration_sec),
                                "size": size_str,
                                "mediaType": "video",
                                "fileExt": "mp4",
                                "filename": safe_name,
                                "downloadUrl": qualities[0]["downloadUrl"],
                                "qualities": qualities,
                                "direct_stream_url": direct_mp4,
                                "engine": "tikwm"
                            }
                except Exception as ep_err:
                    logger.debug("TikWM endpoint %s failed: %s", ep, ep_err)
    except Exception as e:
        logger.warning("TikWM extraction error: %s", e)
    return None


# ─── YouTube Video ID extractor ──────────────────────────────────────────────
def _extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from any YouTube URL format."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


# ─── YouTube Piped API Engine ─────────────────────────────────────────────────
# Piped is an open-source YouTube proxy. Its stream URLs work from ANY IP.
# ─── YouTube Piped API Engine ─────────────────────────────────────────────────
# Piped is an open-source YouTube proxy.
PIPED_INSTANCES = [
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.r4fo.com",
    "https://pipedapi.kavin.rocks",
]

async def extract_youtube_piped(url: str) -> Optional[Dict[str, Any]]:
    """Try Piped API instances with strict 2.5s timeout."""
    vid_id = _extract_youtube_id(url)
    if not vid_id:
        return None

    for instance in PIPED_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
                r = await client.get(
                    f"{instance}/streams/{vid_id}",
                    headers={"User-Agent": BROWSER_UA, "Accept": "application/json"}
                )
                if r.status_code != 200 or not r.text or not r.text.strip():
                    continue
                data = r.json()
                title = data.get("title") or "YouTube Video"
                thumb = data.get("thumbnailUrl") or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                uploader = data.get("uploader") or "YouTube"
                duration_sec = data.get("duration") or 0
                safe_name = f"{_sanitize_title(title)}_YouTube.mp4"
                encoded_url = urllib.parse.quote(url, safe="")

                video_streams = data.get("videoStreams") or []
                muxed = [s for s in video_streams if s.get("videoOnly") is False]
                if not muxed:
                    muxed = video_streams

                muxed.sort(key=lambda s: s.get("quality", "").replace("p", "").split(" ")[0] or "0", reverse=True)

                qualities = []
                seen_heights = set()
                for stream in muxed:
                    stream_url = stream.get("url")
                    if not stream_url:
                        continue
                    q_label = stream.get("quality") or "720p"
                    try:
                        h = int(q_label.replace("p", "").split(" ")[0])
                    except Exception:
                        h = 720
                    if h in seen_heights:
                        continue
                    seen_heights.add(h)
                    encoded_stream = urllib.parse.quote(stream_url, safe="")
                    dl_url = (
                        f"/api/download?url={encoded_url}"
                        f"&direct_url={encoded_stream}"
                        f"&filename={urllib.parse.quote(safe_name, safe='')}"
                        f"&redirect=false"
                    )
                    qualities.append({
                        "label": f"{h}p {'HD' if h >= 720 else 'SD'}",
                        "height": h,
                        "downloadUrl": dl_url,
                        "direct_url": None  # IP-bound Piped URL — hidden from client to prevent HTTP 400
                    })
                    if len(qualities) >= 4:
                        break

                if not qualities:
                    continue

                logger.info("YouTube extracted via Piped instance: %s (video_id=%s)", instance, vid_id)
                return {
                    "success": True,
                    "status": "success",
                    "platform": "YouTube",
                    "title": title,
                    "thumbnail": thumb,
                    "duration": _format_duration(duration_sec),
                    "size": "HD MP4",
                    "mediaType": "video",
                    "fileExt": "mp4",
                    "filename": safe_name,
                    "uploader": uploader,
                    "downloadUrl": qualities[0]["downloadUrl"],
                    "qualities": qualities,
                    "direct_stream_url": None,  # Never expose IP-bound Piped URL — use /api/download proxy
                    "engine": "youtube-piped"
                }
        except Exception:
            continue
    return None


# ─── YouTube Invidious API Engine ─────────────────────────────────────────────
# Invidious instances with active companion PO-Token support
INVIDIOUS_INSTANCES = [
    "https://invidious.f5.si",
    "https://inv.tux.pizza",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
]

async def extract_youtube_invidious(url: str) -> Optional[Dict[str, Any]]:
    """Try Invidious API instances with fast 3.5s timeout. Primary high-speed YouTube engine."""
    vid_id = _extract_youtube_id(url)
    if not vid_id:
        return None

    for instance in INVIDIOUS_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
                r = await client.get(
                    f"{instance}/api/v1/videos/{vid_id}?fields=title,author,lengthSeconds,videoThumbnails,adaptiveFormats,formatStreams",
                    headers={"User-Agent": BROWSER_UA, "Accept": "application/json"}
                )
                if r.status_code != 200 or not r.text or not r.text.strip():
                    continue
                data = r.json()
                title = data.get("title") or "YouTube Video"
                uploader = data.get("author") or "YouTube"
                duration_sec = data.get("lengthSeconds") or 0
                thumbs = data.get("videoThumbnails") or []
                thumb = next((t["url"] for t in thumbs if t.get("quality") == "maxres"), "")
                if not thumb and thumbs:
                    thumb = thumbs[0].get("url", "")
                safe_name = f"{_sanitize_title(title)}_YouTube.mp4"
                encoded_url = urllib.parse.quote(url, safe="")

                # 1. formatStreams = muxed mp4 streams (has audio + video guaranteed)
                format_streams = data.get("formatStreams") or []
                # 2. adaptiveFormats = high resolution mp4 video streams
                adaptive_formats = [
                    f for f in (data.get("adaptiveFormats") or [])
                    if f.get("container") == "mp4" and f.get("url") and f.get("resolution")
                ]

                qualities = []
                seen_heights = set()

                # Add muxed streams first
                for stream in format_streams:
                    stream_url = stream.get("url")
                    if not stream_url:
                        continue
                    res = stream.get("resolution") or stream.get("qualityLabel") or "360p"
                    try:
                        h = int(res.replace("p", "").split(" ")[0])
                    except Exception:
                        h = 360
                    if h in seen_heights:
                        continue
                    seen_heights.add(h)
                    encoded_stream = urllib.parse.quote(stream_url, safe="")
                    dl_url = (
                        f"/api/download?url={encoded_url}"
                        f"&direct_url={encoded_stream}"
                        f"&filename={urllib.parse.quote(safe_name, safe='')}"
                        f"&redirect=false"
                    )
                    qualities.append({
                        "label": f"{h}p {'HD' if h >= 720 else 'SD'}",
                        "height": h,
                        "downloadUrl": dl_url,
                        "direct_url": None  # IP-bound Invidious URL — hidden from client to prevent HTTP 400
                    })

                # Add adaptive mp4 streams (e.g. 720p, 1080p)
                for stream in adaptive_formats:
                    stream_url = stream.get("url")
                    if not stream_url:
                        continue
                    res = stream.get("resolution") or stream.get("qualityLabel") or "720p"
                    try:
                        h = int(res.replace("p", "").split(" ")[0])
                    except Exception:
                        h = 720
                    if h in seen_heights:
                        continue
                    seen_heights.add(h)
                    encoded_stream = urllib.parse.quote(stream_url, safe="")
                    dl_url = (
                        f"/api/download?url={encoded_url}"
                        f"&direct_url={encoded_stream}"
                        f"&filename={urllib.parse.quote(safe_name, safe='')}"
                        f"&redirect=false"
                    )
                    qualities.append({
                        "label": f"{h}p {'Full HD' if h >= 1080 else 'HD' if h >= 720 else 'SD'}",
                        "height": h,
                        "downloadUrl": dl_url,
                        "direct_url": None  # IP-bound Invidious URL — hidden from client to prevent HTTP 400
                    })

                if not qualities:
                    continue

                qualities.sort(key=lambda q: q.get("height", 0), reverse=True)

                logger.info("YouTube extracted via Invidious instance: %s (video_id=%s)", instance, vid_id)
                return {
                    "success": True,
                    "status": "success",
                    "platform": "YouTube",
                    "title": title,
                    "thumbnail": thumb,
                    "duration": _format_duration(duration_sec),
                    "size": "HD MP4",
                    "mediaType": "video",
                    "fileExt": "mp4",
                    "filename": safe_name,
                    "uploader": uploader,
                    "downloadUrl": qualities[0]["downloadUrl"],
                    "qualities": qualities,
                    "direct_stream_url": None,  # Never expose IP-bound CDN URL — client must use /api/download proxy
                    "engine": "youtube-invidious"
                }
        except Exception as e:
            logger.debug("Invidious instance %s error: %s", instance, e)
            continue
    return None


# ─── YouTube oEmbed Fallback (metadata only, proxy download) ─────────────────
async def extract_youtube_oembed(url: str) -> Optional[Dict[str, Any]]:
    """Last-resort: get title/thumb from oEmbed, download via server-side yt-dlp proxy."""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json")
            if r.status_code == 200:
                oe = r.json()
                title = oe.get("title", "YouTube Video")
                thumb = oe.get("thumbnail_url", "")
                author = oe.get("author_name", "YouTube")
                safe_name = f"{_sanitize_title(title)}_YouTube.mp4"
                encoded_url = urllib.parse.quote(url)

                # CRITICAL: redirect=false forces server-side proxy
                # This means Render proxies the YouTube CDN stream → phone never
                # touches the IP-bound CDN URL directly (avoids 403)
                qualities = [
                    {"label": "360p SD", "height": 360,
                     "downloadUrl": f"/api/download?url={encoded_url}&quality=360p&redirect=false"},
                    {"label": "480p SD", "height": 480,
                     "downloadUrl": f"/api/download?url={encoded_url}&quality=480p&redirect=false"},
                    {"label": "720p HD", "height": 720,
                     "downloadUrl": f"/api/download?url={encoded_url}&quality=720p&redirect=false"}
                ]

                return {
                    "success": True,
                    "status": "success",
                    "platform": "YouTube",
                    "title": title,
                    "thumbnail": thumb,
                    "duration": "",
                    "size": "HD MP4",
                    "mediaType": "video",
                    "fileExt": "mp4",
                    "filename": safe_name,
                    "uploader": author,
                    "downloadUrl": qualities[-1]["downloadUrl"],
                    "qualities": qualities,
                    "engine": "youtube-oembed"
                }
    except Exception as e:
        logger.warning("oEmbed fallback error: %s", e)
    return None


# ─── Instagram SnapSave Unpacker & Multi-Engine Extractor ─────────────────────
def _decode_snapsave_packed(js_code: str) -> str:
    """Decodes packed JS response from SnapSave API."""
    try:
        m = re.search(r'\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', js_code)
        if not m:
            return ""
        h = m.group(1)
        u = int(m.group(2))
        n = m.group(3)
        t = int(m.group(4))
        e = int(m.group(5))
        r = int(m.group(6))

        chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
        def _base_conv(d_val, e_val, f_val):
            h_str = chars[:e_val]
            i_str = chars[:f_val]
            j = 0
            for c_idx, char in enumerate(reversed(d_val)):
                idx = h_str.find(char)
                if idx != -1:
                    j += idx * (e_val ** c_idx)
            k = ""
            while j > 0:
                k = i_str[j % f_val] + k
                j = (j - (j % f_val)) // f_val
            return int(k) if k else 0

        res = []
        i = 0
        delimiter = n[e]
        length = len(h)
        while i < length:
            s = ""
            while i < length and h[i] != delimiter:
                s += h[i]
                i += 1
            i += 1
            if s:
                for j_idx in range(len(n)):
                    s = s.replace(n[j_idx], str(j_idx))
                val = _base_conv(s, e, 10) - t
                if val > 0:
                    res.append(chr(val))
        out = "".join(res)
        return urllib.parse.unquote(out)
    except Exception as err:
        logger.debug("SnapSave decode exception: %s", err)
        return ""


async def _extract_instagram_snapsave(url: str, shortcode: str) -> Optional[Dict[str, Any]]:
    """Engine 1: SnapSave Public API."""
    try:
        post_data = {"url": url}
        headers = {
            "User-Agent": BROWSER_UA,
            "Referer": "https://snapsave.app/",
            "Origin": "https://snapsave.app",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.post("https://snapsave.app/action.php", data=post_data, headers=headers)
            if resp.status_code == 200:
                html = _decode_snapsave_packed(resp.text)
                if html:
                    # Look for download links
                    video_links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*download', html)
                    if not video_links:
                        video_links = re.findall(r'href="([^"]+)"[^>]*class="[^"]*button[^"]*download', html)
                    if not video_links:
                        video_links = [l for l in re.findall(r'href="([^"]+)"', html) if "token=" in l or ".mp4" in l or "cdn" in l]

                    if video_links:
                        raw_stream = video_links[0]
                        thumb_m = re.search(r'<img[^>]+src="([^"]+)"', html)
                        thumb = thumb_m.group(1) if thumb_m else ""
                        safe_name = f"Instagram_Reel_{shortcode}.mp4"
                        encoded_url = urllib.parse.quote(url, safe="")
                        encoded_stream = urllib.parse.quote(raw_stream, safe="")

                        return {
                            "success": True,
                            "status": "success",
                            "platform": "Instagram",
                            "title": f"Instagram Reel {shortcode}",
                            "thumbnail": thumb,
                            "duration": "",
                            "size": "HD MP4",
                            "mediaType": "video",
                            "fileExt": "mp4",
                            "filename": safe_name,
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                            "qualities": [
                                {
                                    "label": "HD (SnapSave Direct)",
                                    "height": 720,
                                    "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                                    "direct_url": raw_stream
                                }
                            ],
                            "direct_stream_url": raw_stream,
                            "engine": "snapsave-api"
                        }
    except Exception as e:
        logger.debug("SnapSave extraction failed: %s", e)
    return None


async def _extract_instagram_embed(url: str, shortcode: str) -> Optional[Dict[str, Any]]:
    """Engine 2: Aggressive Instagram Embed and HTML Scraper."""
    try:
        embed_urls = [
            f"https://www.instagram.com/reel/{shortcode}/embed/captioned/",
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            f"https://www.instagram.com/reel/{shortcode}/embed/",
            f"https://www.instagram.com/p/{shortcode}/embed/"
        ]
        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
        }

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for ep in embed_urls:
                res = await client.get(ep, headers=headers)
                if res.status_code == 200:
                    html = res.text
                    raw_video_url = None

                    # Regex patterns for direct mp4 / video_url
                    v1 = re.search(r'"video_url"\s*:\s*"([^"]+)"', html)
                    if v1:
                        raw_video_url = v1.group(1)
                    if not raw_video_url:
                        v2 = re.search(r'class="EmbeddedMediaVideo"[^>]*src="([^"]+)"', html)
                        if v2:
                            raw_video_url = v2.group(1)
                    if not raw_video_url:
                        v3 = re.search(r'\\?"video_url\\?"\s*:\s*\\?"([^"\\]+(?:\\.[^"\\]+)*)\\?"', html)
                        if v3:
                            raw_video_url = v3.group(1)
                    if not raw_video_url:
                        # Any cdn link ending with .mp4 or bytestart
                        mp4_match = re.search(r'(https?:\\?/\\?/[^"\'\s<>]*(?:cdninstagram|fbcdn)[^"\'\s<>]*\.mp4[^"\'\s<>]*)', html)
                        if mp4_match:
                            raw_video_url = mp4_match.group(1)

                    if raw_video_url:
                        raw_video_url = raw_video_url.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")

                        thumb_match = re.search(r'"display_url"\s*:\s*"([^"]+)"', html) or re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html)
                        thumb = thumb_match.group(1).replace("\\u0026", "&").replace("\\/", "/") if thumb_match else ""

                        caption_match = re.search(r'class="Caption"[^>]*>([^<]+)<', html)
                        caption = caption_match.group(1).strip() if caption_match else f"Instagram_Reel_{shortcode}"
                        safe_name = f"{_sanitize_title(caption, 35)}_Instagram.mp4"

                        encoded_url = urllib.parse.quote(url, safe="")
                        encoded_stream = urllib.parse.quote(raw_video_url, safe="")

                        return {
                            "success": True,
                            "status": "success",
                            "platform": "Instagram",
                            "title": caption,
                            "thumbnail": thumb,
                            "duration": "",
                            "size": "HD MP4",
                            "mediaType": "video",
                            "fileExt": "mp4",
                            "filename": safe_name,
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                            "qualities": [
                                {
                                    "label": "HD MP4 (Direct CDN)",
                                    "height": 720,
                                    "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                                    "direct_url": raw_video_url
                                }
                            ],
                            "direct_stream_url": raw_video_url,
                            "engine": "instagram-embed"
                        }
    except Exception as e:
        logger.debug("Instagram embed scraper error: %s", e)
    return None


async def extract_instagram(url: str) -> Optional[Dict[str, Any]]:
    """Extracts public Instagram Reels, Posts, and Videos using multi-engine fallback."""
    # 1. Extract shortcode
    match = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_\-]+)", url)
    if not match:
        return None
    shortcode = match.group(1)

    # Engine 1: SnapSave API
    res = await _extract_instagram_snapsave(url, shortcode)
    if res:
        logger.info("Instagram extracted via SnapSave API")
        return res

    # Engine 2: Embed and HTML regex Scraper
    res = await _extract_instagram_embed(url, shortcode)
    if res:
        logger.info("Instagram extracted via Embed Scraper")
        return res

    return None


# ─── Twitter/X Direct FxTwitter + Syndication CDN Engine ─────────────────────
async def extract_twitter(url: str) -> Optional[Dict[str, Any]]:
    """Extracts Twitter/X videos via FxTwitter API and Syndication API without login."""
    try:
        match = re.search(r"/status/(\d+)", url)
        if not match:
            return None
        tweet_id = match.group(1)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://platform.twitter.com/",
        }

        # 1. Primary Engine: FxTwitter API (Super fast, reliable, direct MP4 stream)
        fx_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            try:
                res_fx = await client.get(fx_url, headers=headers)
                if res_fx.status_code == 200:
                    d_fx = res_fx.json()
                    tweet = d_fx.get("tweet") or {}
                    media = tweet.get("media") or {}
                    videos = media.get("videos") or []
                    if not videos and media.get("all"):
                        videos = [m for m in media.get("all", []) if m.get("type") in ("video", "gif")]
                    if videos:
                        best_vid = videos[0]
                        best_url = best_vid.get("url")
                        if best_url:
                            thumb = best_vid.get("thumbnail_url") or ""
                            text = tweet.get("text") or f"Twitter_Video_{tweet_id}"
                            user = tweet.get("author", {}).get("name") or "Twitter"
                            safe_name = f"{_sanitize_title(text, 35)}_Twitter.mp4"

                            encoded_url = urllib.parse.quote(url, safe="")
                            encoded_stream = urllib.parse.quote(best_url, safe="")

                            qualities = [
                                {
                                    "label": "HD MP4 (Direct CDN)",
                                    "height": 720,
                                    "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                                    "direct_url": best_url
                                }
                            ]

                            return {
                                "success": True,
                                "status": "success",
                                "platform": "Twitter",
                                "title": text,
                                "thumbnail": thumb,
                                "duration": "",
                                "size": "HD MP4",
                                "mediaType": "video",
                                "fileExt": "mp4",
                                "filename": safe_name,
                                "uploader": user,
                                "downloadUrl": qualities[0]["downloadUrl"],
                                "qualities": qualities,
                                "direct_stream_url": best_url,
                                "engine": "fxtwitter-direct"
                            }
            except Exception as e_fx:
                logger.warning("FxTwitter extraction error: %s", e_fx)

        # 2. Secondary Engine: Syndication API Fallback
        api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.get(api_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    media_list = data.get("mediaDetails") or []
                    text = data.get("text") or f"Twitter_Video_{tweet_id}"
                    user = data.get("user", {}).get("name") or "Twitter"

                    for media in media_list:
                        if media.get("type") == "video":
                            variants = media.get("video_info", {}).get("variants") or []
                            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
                            if mp4_variants:
                                mp4_variants.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                                best = mp4_variants[0]
                                best_url = best["url"]
                                thumb = media.get("media_url_https") or ""
                                safe_name = f"{_sanitize_title(text, 35)}_Twitter.mp4"

                                encoded_url = urllib.parse.quote(url, safe="")
                                encoded_stream = urllib.parse.quote(best_url, safe="")

                                qualities = [
                                    {
                                        "label": "HD 720p (Direct CDN)",
                                        "height": 720,
                                        "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                                        "direct_url": best_url
                                    }
                                ]

                                return {
                                    "success": True,
                                    "status": "success",
                                    "platform": "Twitter",
                                    "title": text,
                                    "thumbnail": thumb,
                                    "duration": "",
                                    "size": "HD MP4",
                                    "mediaType": "video",
                                    "fileExt": "mp4",
                                    "filename": safe_name,
                                    "uploader": user,
                                    "downloadUrl": qualities[0]["downloadUrl"],
                                    "qualities": qualities,
                                    "direct_stream_url": best_url,
                                    "engine": "twitter-syndication"
                                }
    except Exception as e:
        logger.warning("Twitter syndication extraction error: %s", e)
    return None


# ─── Facebook Public Direct Scraper Engine ───────────────────────────────────
async def extract_facebook(url: str) -> Optional[Dict[str, Any]]:
    """Extracts public Facebook video direct CDN links."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Site": "none",
        }
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                html = res.text
                hd_match = re.search(r'hd_src\s*:\s*"([^"]+)"', html) or re.search(r'"browser_native_hd_url"\s*:\s*"([^"]+)"', html)
                sd_match = re.search(r'sd_src\s*:\s*"([^"]+)"', html) or re.search(r'"browser_native_sd_url"\s*:\s*"([^"]+)"', html)

                video_url = None
                if hd_match:
                    video_url = hd_match.group(1).replace("\\/", "/")
                elif sd_match:
                    video_url = sd_match.group(1).replace("\\/", "/")

                if video_url:
                    title_match = re.search(r'<title>(.*?)</title>', html)
                    title = title_match.group(1).strip() if title_match else "Facebook Video"
                    safe_name = f"{_sanitize_title(title, 35)}_Facebook.mp4"

                    encoded_url = urllib.parse.quote(url, safe="")
                    encoded_stream = urllib.parse.quote(video_url, safe="")

                    qualities = [
                        {
                            "label": "HD MP4 (Direct CDN)",
                            "height": 720,
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                            "direct_url": video_url
                        }
                    ]

                    return {
                        "success": True,
                        "status": "success",
                        "platform": "Facebook",
                        "title": title,
                        "thumbnail": "",
                        "duration": "",
                        "size": "HD MP4",
                        "mediaType": "video",
                        "fileExt": "mp4",
                        "filename": safe_name,
                        "downloadUrl": qualities[0]["downloadUrl"],
                        "qualities": qualities,
                        "direct_stream_url": video_url,
                        "engine": "facebook-direct"
                    }
    except Exception as e:
        logger.warning("Facebook direct extraction error: %s", e)
    return None


# ─── Generic yt-dlp Extraction Engine (Speed-Optimized) ────────────────────
def extract_ytdlp(url: str, platform: str, cookie_file: Optional[str] = None) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 12,          # reduced from 15
        "http_headers": {
            "User-Agent": BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9"
        },
        # Skip writing download archive — saves disk I/O on every call
        "skip_download": True,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file

    if platform == "YouTube":
        # visionos client bypasses bot-detection and PO-Token restrictions on datacenter IPs
        opts["extractor_args"] = {"youtube": {"player_client": ["visionos", "mweb", "ios"]}}
    elif platform == "Instagram":
        opts["http_headers"]["User-Agent"] = MOBILE_UA
        opts["http_headers"]["X-IG-App-ID"] = "936619743392459"
        opts["http_headers"]["Referer"] = "https://www.instagram.com/"
        session_id = os.environ.get("INSTAGRAM_SESSION_ID", "").strip()
        if session_id:
            opts["http_headers"]["Cookie"] = f"sessionid={session_id};"
    elif platform == "TikTok":
        # TikTok works best with mobile UA via yt-dlp fallback
        opts["http_headers"]["User-Agent"] = MOBILE_UA
    elif platform == "Twitter":
        opts["http_headers"]["Referer"] = "https://platform.twitter.com/"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info


# ─── Safe Proxy Streamer — prevents 502 & connection leaks ──────────────────
async def _safe_proxy_stream(
    stream_url: str,
    headers: Dict[str, str],
    safe_name: str,
    redirect_fallback: bool = True,
    redirect_url: Optional[str] = None,
):
    """
    Proxy a remote stream with guaranteed cleanup.
    - Uses explicit aclose() to ensure connections never leak (prevents 502 hang)
    - Upstream 4xx/5xx → clean redirect or 502 error instead of hanging
    - Sets all required headers for Android DownloadManager
    """
    actual_redirect_url = redirect_url or stream_url
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=120.0),
        follow_redirects=True
    )
    try:
        req = client.build_request("GET", stream_url, headers=headers)
        resp = await client.send(req, stream=True)

        if resp.status_code >= 400:
            await resp.aclose()
            await client.aclose()
            logger.warning("Upstream proxy returned HTTP %d", resp.status_code)
            if redirect_fallback:
                return RedirectResponse(url=actual_redirect_url, status_code=302)
            return JSONResponse(
                status_code=502,
                content={"error": f"Upstream stream returned HTTP {resp.status_code}"}
            )

        content_length = resp.headers.get("content-length")
        content_type = resp.headers.get("content-type") or "video/mp4"

        async def _stream_generator():
            try:
                async for chunk in resp.aiter_bytes(131072):
                    yield chunk
            except Exception as stream_err:
                logger.warning("Stream interrupted: %s", stream_err)
            finally:
                await resp.aclose()
                await client.aclose()

        resp_headers = {
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        }
        if content_length:
            resp_headers["Content-Length"] = content_length

        return StreamingResponse(_stream_generator(), media_type=content_type, headers=resp_headers)

    except Exception as e:
        logger.warning("Proxy connection failed: %s", e)
        try:
            await client.aclose()
        except Exception:
            pass
        if redirect_fallback:
            return RedirectResponse(url=actual_redirect_url, status_code=302)
        return JSONResponse(status_code=502, content={"error": f"Proxy connection failed: {str(e)}"})


# ─── Lifespan & FastAPI Setup ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    cf = _init_cookie_file()
    has_cookies = bool(cf)
    logger.info("Down4U Universal Backend v5.0.0 starting. Cookies configured: %s", has_cookies)
    yield
    logger.info("Down4U Backend stopping.")


app = FastAPI(title="Down4U Pro API", version="5.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "Content-Type"]
)


# ─── Health Checks ───────────────────────────────────────────────────────────
@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health():
    return {
        "status": "online",
        "app": "Down4U Pro Video Downloader",
        "version": "5.2.0",
        "engines": ["TikWM", "Invidious", "Piped", "FxTwitter", "yt-dlp", "oEmbed"],
        "cookies": bool(os.environ.get(COOKIES_ENV))
    }


# ─── /api/info Endpoint (Speed-Optimized: Parallel Extraction) ───────────────
@app.get("/api/info")
async def get_info(url: str = Query(...)):
    url = url.strip()
    if not url:
        return JSONResponse(status_code=400, content={"error": "URL is required"})

    platform = detect_platform(url)
    logger.info("Handling /api/info for platform=%s url=%s", platform, url[:80])

    # ── FAST PATH: platform-specific extractors (lightweight HTTP, no yt-dlp) ──
    # TikTok, Instagram, Twitter, Facebook — these run FIRST and are very fast
    # (TikWM API ~300ms, Instagram embed ~400ms, Twitter syndication ~250ms)
    # Only fall through to yt-dlp if they fail

    if platform == "TikTok":
        tt_res = await extract_tiktok(url)
        if tt_res:
            logger.info("TikTok extracted via TikWM in fast path")
            return JSONResponse(content=tt_res)

    elif platform == "Instagram":
        ig_res = await extract_instagram(url)
        if ig_res:
            logger.info("Instagram extracted via embed scraper in fast path")
            return JSONResponse(content=ig_res)

    elif platform == "Twitter":
        tw_res = await extract_twitter(url)
        if tw_res:
            logger.info("Twitter extracted via syndication API in fast path")
            return JSONResponse(content=tw_res)

    elif platform == "Facebook":
        fb_res = await extract_facebook(url)
        if fb_res:
            logger.info("Facebook extracted via public scraper in fast path")
            return JSONResponse(content=fb_res)

    # ── PARALLEL PATH: Race Invidious vs Piped (YouTube) or yt-dlp fallback ──
    cf = _write_cookie_file()
    ytdlp_error = None

    if platform == "YouTube":
        # Run Invidious and Piped simultaneously — first to succeed wins
        async def _try_invidious():
            try:
                return await asyncio.wait_for(extract_youtube_invidious(url), timeout=5.0)
            except Exception:
                return None

        async def _try_piped():
            try:
                return await asyncio.wait_for(extract_youtube_piped(url), timeout=4.0)
            except Exception:
                return None

        invidious_task = asyncio.create_task(_try_invidious())
        piped_task = asyncio.create_task(_try_piped())

        done, pending = await asyncio.wait(
            [invidious_task, piped_task],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=5.5
        )

        yt_alt = None
        for task in list(done):
            try:
                result = task.result()
                if result:
                    yt_alt = result
                    break
            except Exception:
                pass

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass

        # If first done failed, check remaining
        if not yt_alt:
            remaining = [t for t in done if t not in [invidious_task, piped_task] or True]
            for task in list(done):
                if not task.cancelled():
                    try:
                        result = task.result()
                        if result:
                            yt_alt = result
                            break
                    except Exception:
                        pass

        if yt_alt:
            logger.info("YouTube extracted via fast external resolver: %s", yt_alt.get("engine"))
            return JSONResponse(content=yt_alt)

    # ── yt-dlp fallback with strict timeout ──
    async def _run_ytdlp():
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(extract_ytdlp, url, platform, cf),
                timeout=12.0
            )
        except asyncio.TimeoutError:
            return Exception("Extraction timed out after 12s")
        except Exception as e:
            return e

    ytdlp_result = await _run_ytdlp()

    oembed_result = None
    if platform == "YouTube" and (isinstance(ytdlp_result, Exception) or not ytdlp_result):
        oembed_result = await extract_youtube_oembed(url)

    # Process yt-dlp result
    if isinstance(ytdlp_result, Exception):
        ytdlp_error = str(ytdlp_result)
        logger.warning("yt-dlp extraction failed for %s: %s", platform, ytdlp_result)
    elif ytdlp_result:
        try:
            info = ytdlp_result
            title = info.get("title") or f"{platform} Video"
            thumbnail = info.get("thumbnail") or ""
            duration_sec = info.get("duration") or 0
            duration_str = _format_duration(duration_sec)
            safe_name = f"{_sanitize_title(title)}_{platform}.mp4"

            formats = info.get("formats") or []
            heights = set()
            for f in formats:
                h = f.get("height")
                if isinstance(h, int) and h > 0:
                    heights.add(h)

            std_heights = [144, 360, 480, 720, 1080]
            max_h = max(heights) if heights else 720
            usable_heights = sorted([h for h in std_heights if h <= max(max_h, 720)])
            if not usable_heights:
                usable_heights = [360, 480, 720]

            stream_url = info.get("url")
            rf = info.get("requested_formats")
            if rf and isinstance(rf, list) and len(rf) > 0:
                stream_url = rf[0].get("url") or stream_url

            size_bytes = info.get("filesize") or info.get("filesize_approx") or 0
            size_str = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes else "HD MP4"

            encoded_url = urllib.parse.quote(url)
            # YouTube CDN URLs are IP-bound to Render server — always proxy, never expose to client
            is_yt = (platform == "YouTube")
            qualities = []
            for h in usable_heights:
                f_match = next(
                    (f for f in formats if f.get("height") == h and f.get("url")
                     and f.get("vcodec") != "none" and f.get("acodec") != "none"), None
                )
                if not f_match:
                    f_match = next((f for f in formats if f.get("height") == h and f.get("url")), None)
                d_url = f_match.get("url") if f_match else (stream_url or "")
                if is_yt:
                    # Force server-side proxy; do NOT expose IP-bound googlevideo URL to device
                    dl_url = (
                        f"/api/download?url={encoded_url}&quality={h}p"
                        f"&filename={urllib.parse.quote(safe_name)}&redirect=false"
                        + (f"&direct_url={urllib.parse.quote(d_url, safe='')}" if d_url else "")
                    )
                    exposed_direct = None  # hide from client — IP-bound, would 403 on device
                else:
                    dl_url = (
                        f"/api/download?url={encoded_url}&quality={h}p"
                        f"&filename={urllib.parse.quote(safe_name)}"
                        + (f"&direct_url={urllib.parse.quote(d_url, safe='')}" if d_url else "")
                    )
                    exposed_direct = d_url
                qualities.append({
                    "label": f"{h}p {'Full HD' if h >= 1080 else 'HD' if h >= 720 else 'SD'}",
                    "height": h,
                    "downloadUrl": dl_url,
                    "direct_url": exposed_direct
                })

            return JSONResponse(content={
                "success": True,
                "status": "success",
                "platform": platform,
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration_str,
                "size": size_str,
                "mediaType": "video",
                "fileExt": "mp4",
                "filename": safe_name,
                "downloadUrl": qualities[0]["downloadUrl"] if qualities else "",
                "qualities": qualities,
                # YouTube: hide IP-bound CDN URL — client must use /api/download proxy
                "direct_stream_url": None if is_yt else stream_url,
                "engine": "yt-dlp"
            })
        except Exception as parse_err:
            ytdlp_error = str(parse_err)
            logger.warning("Error parsing yt-dlp result for %s: %s", platform, parse_err)

    # oEmbed fallback (ran in parallel, already done)
    if oembed_result:
        return JSONResponse(content=oembed_result)

    # All engines failed — return descriptive error
    err_lower = (ytdlp_error or "").lower()
    if "429" in err_lower or "too many" in err_lower:
        msg = "Rate limited by platform. Please try again in a moment."
    elif "private" in err_lower or "login" in err_lower:
        msg = "This video is private or requires login."
    elif "unavailable" in err_lower or "deleted" in err_lower:
        msg = "This video is unavailable or has been deleted."
    else:
        msg = f"Could not extract video from {platform}. Please ensure the link is public."

    return JSONResponse(
        status_code=400,
        content={"success": False, "status": "error", "error": msg, "details": ytdlp_error}
    )


# ─── /api/download and /api/stream Endpoints ─────────────────────────────────
@app.get("/api/download")
@app.get("/api/stream")
async def download_video(
    url: Optional[str] = Query(None),
    videoUrl: Optional[str] = Query(None),
    direct_url: Optional[str] = Query(None),
    src: Optional[str] = Query(None),
    quality: str = Query("720p"),
    filename: Optional[str] = Query(None),
    redirect: bool = Query(True)
):
    target_url = url or videoUrl
    raw_stream_url = direct_url or src
    platform = detect_platform(target_url or "") if target_url else "Web"
    safe_name = filename or f"video_{platform}.mp4"

    # ── FAST PATH: direct_url known — proxy or redirect (with guaranteed cleanup) ──
    if raw_stream_url:
        logger.info("[FAST] Direct CDN stream for %s platform=%s", safe_name, platform)
        is_youtube_stream = platform == "YouTube" or "googlevideo.com" in raw_stream_url

        if redirect and not is_youtube_stream:
            return RedirectResponse(url=raw_stream_url, status_code=302)

        proxy_headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive"
        }
        if "cdninstagram.com" in raw_stream_url or "fbcdn.net" in raw_stream_url:
            proxy_headers["Referer"] = "https://www.instagram.com/"
        elif "twimg.com" in raw_stream_url:
            proxy_headers["Referer"] = "https://twitter.com/"
        elif "tiktokcdn.com" in raw_stream_url:
            proxy_headers["Referer"] = "https://www.tiktok.com/"

        proxy_result = await _safe_proxy_stream(
            stream_url=raw_stream_url,
            headers=proxy_headers,
            safe_name=safe_name,
            redirect_fallback=not is_youtube_stream,
            redirect_url=raw_stream_url if not is_youtube_stream else None,
        )

        # If YouTube CDN URL expired (4xx), fall through to fresh resolution
        if is_youtube_stream and isinstance(proxy_result, JSONResponse) and proxy_result.status_code >= 400:
            raw_stream_url = None
        else:
            return proxy_result

    if not target_url:
        return JSONResponse(status_code=400, content={"error": "URL is required for download"})

    # ── Platform-specific fast paths (all use _safe_proxy_stream) ──
    if platform == "TikTok":
        tt = await extract_tiktok(target_url)
        if tt and tt.get("direct_stream_url"):
            d_url = tt["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            return await _safe_proxy_stream(
                d_url, {"User-Agent": BROWSER_UA, "Referer": "https://www.tiktok.com/"},
                safe_name, redirect_fallback=True, redirect_url=d_url
            )

    if platform == "Instagram":
        ig = await extract_instagram(target_url)
        if ig and ig.get("direct_stream_url"):
            d_url = ig["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            return await _safe_proxy_stream(
                d_url, {"User-Agent": BROWSER_UA, "Referer": "https://www.instagram.com/"},
                safe_name, redirect_fallback=True, redirect_url=d_url
            )

    if platform == "Twitter":
        tw = await extract_twitter(target_url)
        if tw and tw.get("direct_stream_url"):
            d_url = tw["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            return await _safe_proxy_stream(
                d_url, {"User-Agent": BROWSER_UA, "Referer": "https://twitter.com/"},
                safe_name, redirect_fallback=True, redirect_url=d_url
            )

    if platform == "Facebook":
        fb = await extract_facebook(target_url)
        if fb and fb.get("direct_stream_url"):
            d_url = fb["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            return await _safe_proxy_stream(
                d_url, {"User-Agent": BROWSER_UA},
                safe_name, redirect_fallback=True, redirect_url=d_url
            )

    # ── YouTube stream resolution: Invidious || Piped parallel race, then yt-dlp ──
    stream_url = None
    if platform == "YouTube" and target_url:
        logger.info("Resolving YouTube stream for download: %s quality=%s", target_url, quality)
        target_h = int(quality[:-1]) if quality.endswith("p") and quality[:-1].isdigit() else 720
        try:
            inv_task = asyncio.create_task(extract_youtube_invidious(target_url))
            piped_task = asyncio.create_task(extract_youtube_piped(target_url))
            done, pending = await asyncio.wait([inv_task, piped_task], return_when=asyncio.FIRST_COMPLETED, timeout=6.0)

            for task in list(done):
                try:
                    res = task.result()
                    if res:
                        q_match = next(
                            (q["direct_url"] for q in res.get("qualities", [])
                             if q.get("height") == target_h and q.get("direct_url")),
                            None
                        )
                        if not q_match and res.get("qualities"):
                            q_match = res["qualities"][0].get("direct_url")
                        stream_url = q_match or res.get("direct_stream_url")
                        if stream_url:
                            break
                except (Exception, asyncio.CancelledError):
                    pass

            for task in pending:
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    pass
        except Exception as e_race:
            logger.warning("Fast resolver race failed in download: %s", e_race)

    if not stream_url:
        cf = _write_cookie_file()
        h = int(quality[:-1]) if quality.endswith("p") and quality[:-1].isdigit() else 720
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "format": f"best[height<={h}][ext=mp4]/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}]/best",
            "merge_output_format": "mp4",
            "http_headers": {
                "User-Agent": BROWSER_UA,
                "Accept-Language": "en-US,en;q=0.9"
            }
        }
        if cf:
            opts["cookiefile"] = cf
        if platform == "YouTube":
            opts["extractor_args"] = {"youtube": {"player_client": ["android", "ios", "mweb", "web"]}}
        elif platform == "Instagram":
            opts["http_headers"]["User-Agent"] = MOBILE_UA
            opts["http_headers"]["X-IG-App-ID"] = "936619743392459"

        try:
            def _get_stream():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(target_url, download=False)
                    rf = info.get("requested_formats")
                    if rf and isinstance(rf, list) and len(rf) > 0:
                        return rf[0].get("url") or info.get("url")
                    return info.get("url")

            stream_url = await asyncio.wait_for(asyncio.to_thread(_get_stream), timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("yt-dlp timed out for download: %s", target_url)
        except Exception as e:
            logger.error("Download extraction failed for %s: %s", target_url, e)

    if stream_url:
        should_redirect = redirect and platform != "YouTube"
        if should_redirect:
            return RedirectResponse(url=stream_url, status_code=302)

        yt_headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }

        return await _safe_proxy_stream(
            stream_url=stream_url,
            headers=yt_headers,
            safe_name=safe_name,
            redirect_fallback=(platform != "YouTube"),
            redirect_url=stream_url if platform != "YouTube" else None,
        )

    # Final emergency fallback: redirect to target_url or return clear actionable error
    logger.error("Could not obtain video stream for %s (platform=%s)", target_url, platform)
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "status": "error",
            "error": f"Unable to stream {platform} video directly. The link might be expired or restricted.",
            "target_url": target_url
        }
    )
