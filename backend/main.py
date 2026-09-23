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
    """Write cookie file once at startup and cache the path."""
    global _COOKIE_FILE_PATH
    if _COOKIE_FILE_PATH:
        return _COOKIE_FILE_PATH
    raw = os.environ.get(COOKIES_ENV, "").strip()
    if not raw:
        return None
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        if not raw.startswith("# Netscape"):
            tmp.write("# Netscape HTTP Cookie File\n")
        tmp.write(raw)
        tmp.flush()
        tmp.close()
        _COOKIE_FILE_PATH = tmp.name
        logger.info("Cookie file written and cached: %s", tmp.name)
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
            res = await client.post("https://www.tikwm.com/api/", data={"url": url}, headers=headers)
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
    except Exception as e:
        logger.warning("TikWM extraction error: %s", e)
    return None


# ─── YouTube oEmbed Fallback ─────────────────────────────────────────────────
async def extract_youtube_oembed(url: str) -> Optional[Dict[str, Any]]:
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

                qualities = [
                    {"label": "360p SD", "height": 360, "downloadUrl": f"/api/download?url={encoded_url}&quality=360p"},
                    {"label": "480p SD", "height": 480, "downloadUrl": f"/api/download?url={encoded_url}&quality=480p"},
                    {"label": "720p HD", "height": 720, "downloadUrl": f"/api/download?url={encoded_url}&quality=720p"}
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


# ─── Instagram Embed & Public Scraper Engine ─────────────────────────────────
async def extract_instagram(url: str) -> Optional[Dict[str, Any]]:
    """Extracts public Instagram Reels, Posts, and Videos without login/cookies."""
    try:
        # 1. Extract shortcode
        match = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_\-]+)", url)
        if not match:
            return None
        shortcode = match.group(1)
        embed_url = f"https://www.instagram.com/reel/{shortcode}/embed/captioned/"
        
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
        }

        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            res = await client.get(embed_url, headers=headers)
            if res.status_code == 200:
                html = res.text
                
                # Check for video URL in embed HTML
                video_match = re.search(r'"video_url"\s*:\s*"([^"]+)"', html)
                if not video_match:
                    video_match = re.search(r'class="EmbeddedMediaVideo"[^>]*src="([^"]+)"', html)
                if not video_match:
                    # Alternative pattern inside JSON blob or window.__additionalDataLoaded
                    video_match = re.search(r'\\?"video_url\\?"\s*:\s*\\?"([^"\\]+(?:\\.[^"\\]+)*)\\?"', html)

                if video_match:
                    raw_video_url = video_match.group(1).replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
                    
                    # Thumbnail
                    thumb_match = re.search(r'"display_url"\s*:\s*"([^"]+)"', html)
                    if not thumb_match:
                        thumb_match = re.search(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"', html)
                    thumb = (
                        thumb_match.group(1).replace("\\u0026", "&").replace("\\/", "/")
                        if thumb_match else ""
                    )
                    
                    # Caption / Title
                    caption_match = re.search(r'class="Caption"[^>]*>([^<]+)<', html)
                    caption = caption_match.group(1).strip() if caption_match else f"Instagram_Reel_{shortcode}"
                    safe_name = f"{_sanitize_title(caption, 35)}_Instagram.mp4"

                    encoded_url = urllib.parse.quote(url, safe="")
                    encoded_stream = urllib.parse.quote(raw_video_url, safe="")

                    qualities = [
                        {
                            "label": "HD MP4 (Direct CDN)",
                            "height": 720,
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}",
                            "direct_url": raw_video_url
                        }
                    ]

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
                        "downloadUrl": qualities[0]["downloadUrl"],
                        "qualities": qualities,
                        "direct_stream_url": raw_video_url,
                        "engine": "instagram-embed"
                    }
    except Exception as e:
        logger.warning("Instagram embed extraction error: %s", e)
    return None


# ─── Twitter/X Direct Syndication CDN Engine ─────────────────────────────────
async def extract_twitter(url: str) -> Optional[Dict[str, Any]]:
    """Extracts Twitter/X videos via Twitter Syndication API without login/token."""
    try:
        match = re.search(r"/status/(\d+)", url)
        if not match:
            return None
        tweet_id = match.group(1)
        api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://platform.twitter.com/",
        }

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.get(api_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                media_list = data.get("mediaDetails") or []
                text = data.get("text") or f"Twitter_Video_{tweet_id}"
                user = data.get("user", {}).get("name") or "Twitter"

                for media in media_list:
                    if media.get("type") == "video":
                        variants = media.get("video_info", {}).get("variants") or []
                        mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
                        if mp4_variants:
                            # Sort by bitrate highest first
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
        # android + ios clients are faster and bypass bot checks better than web
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "ios"]}}
    elif platform == "Instagram":
        opts["http_headers"]["User-Agent"] = MOBILE_UA
        opts["http_headers"]["X-IG-App-ID"] = "936619743392459"
        opts["http_headers"]["Referer"] = "https://www.instagram.com/"
    elif platform == "TikTok":
        # TikTok works best with mobile UA via yt-dlp fallback
        opts["http_headers"]["User-Agent"] = MOBILE_UA
    elif platform == "Twitter":
        opts["extractor_args"] = {"twitter": {"api": ["graphql"]}}

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info


# ─── Lifespan & FastAPI Setup ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Write cookie file ONCE at startup — cached for all subsequent requests
    cf = _init_cookie_file()
    has_cookies = bool(cf)
    logger.info("Down4U Universal Backend starting. Cookies configured: %s", has_cookies)
    yield
    logger.info("Down4U Backend stopping.")


app = FastAPI(title="Down4U Pro API", version="4.0.0", lifespan=lifespan)
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
        "version": "4.0.0",
        "engines": ["TikWM", "yt-dlp", "oEmbed"],
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

    # ── PARALLEL PATH: yt-dlp + oEmbed simultaneously for YouTube/Web/fallbacks ─
    # asyncio.gather runs both at the same time — faster than sequential
    cf = _write_cookie_file()
    ytdlp_error = None

    async def _run_ytdlp():
        try:
            return await asyncio.to_thread(extract_ytdlp, url, platform, cf)
        except Exception as e:
            return e

    async def _run_oembed():
        if platform == "YouTube":
            return await extract_youtube_oembed(url)
        return None

    # Run yt-dlp and oEmbed in parallel
    ytdlp_result, oembed_result = await asyncio.gather(_run_ytdlp(), _run_oembed())

    # Process yt-dlp result
    if isinstance(ytdlp_result, Exception):
        ytdlp_error = str(ytdlp_result)
        logger.warning("yt-dlp extraction failed for %s: %s", platform, ytdlp_result)
    elif ytdlp_result:
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
        qualities = []
        for h in usable_heights:
            f_match = next(
                (f for f in formats if f.get("height") == h and f.get("url")
                 and f.get("vcodec") != "none" and f.get("acodec") != "none"), None
            )
            if not f_match:
                f_match = next((f for f in formats if f.get("height") == h and f.get("url")), None)
            d_url = f_match.get("url") if f_match else (stream_url or "")
            qualities.append({
                "label": f"{h}p {'Full HD' if h >= 1080 else 'HD' if h >= 720 else 'SD'}",
                "height": h,
                "downloadUrl": f"/api/download?url={encoded_url}&quality={h}p"
                               f"&filename={urllib.parse.quote(safe_name)}"
                               + (f"&direct_url={urllib.parse.quote(d_url, safe='')}" if d_url else ""),
                "direct_url": d_url
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
            "direct_stream_url": stream_url,
            "engine": "yt-dlp"
        })

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

    # ── FAST PATH: direct_url already known — immediately redirect, no extractor ──
    # This is the normal path for TikTok, Instagram, Twitter, Facebook, and any
    # platform where /api/info already resolved the CDN URL and embedded it.
    if raw_stream_url:
        logger.info("[FAST] Direct CDN redirect for %s platform=%s", safe_name, platform)
        if redirect:
            return RedirectResponse(url=raw_stream_url, status_code=302)
        # Proxy mode (non-redirect)
        try:
            async def _proxy_direct():
                async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
                    async with client.stream(
                        "GET", raw_stream_url,
                        headers={"User-Agent": BROWSER_UA, "Accept": "video/mp4,video/*;q=0.9,*/*"}
                    ) as resp:
                        async for chunk in resp.aiter_bytes(131072):  # 128KB chunks for speed
                            yield chunk
            return StreamingResponse(
                _proxy_direct(),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}"',
                    "Content-Type": "video/mp4",
                    "Access-Control-Allow-Origin": "*"
                }
            )
        except Exception as e:
            logger.warning("Proxy stream failed (%s), falling back to redirect", e)
            return RedirectResponse(url=raw_stream_url, status_code=302)

    if not target_url:
        return JSONResponse(status_code=400, content={"error": "URL is required for download"})

    # 2. Check TikTok via TikWM
    if platform == "TikTok":
        tt = await extract_tiktok(target_url)
        if tt and tt.get("direct_stream_url"):
            d_url = tt["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            try:
                async def _proxy_tt():
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                        async with client.stream("GET", d_url, headers={"User-Agent": BROWSER_UA}) as resp:
                            async for chunk in resp.aiter_bytes(65536):
                                yield chunk

                return StreamingResponse(
                    _proxy_tt(),
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_name}"',
                        "Content-Type": "video/mp4",
                        "Access-Control-Allow-Origin": "*"
                    }
                )
            except Exception as e_tt:
                logger.warning("TikTok stream proxy failed (%s), redirecting", e_tt)
                return RedirectResponse(url=d_url, status_code=302)

    # 3. Check Instagram Embed / Public Scraper
    if platform == "Instagram":
        ig = await extract_instagram(target_url)
        if ig and ig.get("direct_stream_url"):
            d_url = ig["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            try:
                async def _proxy_ig():
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                        async with client.stream("GET", d_url, headers={"User-Agent": BROWSER_UA}) as resp:
                            async for chunk in resp.aiter_bytes(65536):
                                yield chunk

                return StreamingResponse(
                    _proxy_ig(),
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_name}"',
                        "Content-Type": "video/mp4",
                        "Access-Control-Allow-Origin": "*"
                    }
                )
            except Exception as e_ig:
                logger.warning("Instagram stream proxy failed (%s), redirecting", e_ig)
                return RedirectResponse(url=d_url, status_code=302)

    # 4. Check Twitter / X Syndication Scraper
    if platform == "Twitter":
        tw = await extract_twitter(target_url)
        if tw and tw.get("direct_stream_url"):
            d_url = tw["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            try:
                async def _proxy_tw():
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                        async with client.stream("GET", d_url, headers={"User-Agent": BROWSER_UA}) as resp:
                            async for chunk in resp.aiter_bytes(65536):
                                yield chunk

                return StreamingResponse(
                    _proxy_tw(),
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_name}"',
                        "Content-Type": "video/mp4",
                        "Access-Control-Allow-Origin": "*"
                    }
                )
            except Exception as e_tw:
                logger.warning("Twitter stream proxy failed (%s), redirecting", e_tw)
                return RedirectResponse(url=d_url, status_code=302)

    # 5. Check Facebook Public CDN Scraper
    if platform == "Facebook":
        fb = await extract_facebook(target_url)
        if fb and fb.get("direct_stream_url"):
            d_url = fb["direct_stream_url"]
            if redirect:
                return RedirectResponse(url=d_url, status_code=302)
            try:
                async def _proxy_fb():
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                        async with client.stream("GET", d_url, headers={"User-Agent": BROWSER_UA}) as resp:
                            async for chunk in resp.aiter_bytes(65536):
                                yield chunk

                return StreamingResponse(
                    _proxy_fb(),
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_name}"',
                        "Content-Type": "video/mp4",
                        "Access-Control-Allow-Origin": "*"
                    }
                )
            except Exception as e_fb:
                logger.warning("Facebook stream proxy failed (%s), redirecting", e_fb)
                return RedirectResponse(url=d_url, status_code=302)

    # 6. Resolve stream URL via yt-dlp
    cf = _write_cookie_file()
    h = int(quality[:-1]) if quality.endswith("p") and quality[:-1].isdigit() else 720
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
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
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "ios"]}}
    elif platform == "Instagram":
        opts["http_headers"]["User-Agent"] = MOBILE_UA
        opts["http_headers"]["X-IG-App-ID"] = "936619743392459"

    stream_url = None
    try:
        def _get_stream():
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                rf = info.get("requested_formats")
                if rf and isinstance(rf, list) and len(rf) > 0:
                    return rf[0].get("url") or info.get("url")
                return info.get("url")

        stream_url = await asyncio.to_thread(_get_stream)
    except Exception as e:
        logger.error("Download extraction failed for %s: %s", target_url, e)

    if stream_url:
        if redirect:
            return RedirectResponse(url=stream_url, status_code=302)
        async def _proxy_stream():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", stream_url, headers={"User-Agent": BROWSER_UA}) as resp:
                    async for chunk in resp.aiter_bytes(65536):
                        yield chunk

        return StreamingResponse(
            _proxy_stream(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "Content-Type": "video/mp4",
                "Access-Control-Allow-Origin": "*"
            }
        )

    return JSONResponse(
        status_code=502,
        content={"error": f"Failed to generate download stream for {platform} video."}
    )
