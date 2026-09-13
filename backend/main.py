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

# ─── Cookie File Handler ─────────────────────────────────────────────────────
def _write_cookie_file() -> Optional[str]:
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
        logger.info("Cookie file written: %s", tmp.name)
        return tmp.name
    except Exception as e:
        logger.warning("Failed to write cookie file: %s", e)
        return None


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
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={encoded_stream}&filename={urllib.parse.quote(safe_name, safe='')}"
                        }
                    ]
                    if data.get("wmplay"):
                        qualities.append({
                            "label": "Standard (Watermark)",
                            "height": 540,
                            "downloadUrl": f"/api/download?url={encoded_url}&direct_url={urllib.parse.quote(data['wmplay'], safe='')}&filename={urllib.parse.quote(safe_name, safe='')}"
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
                    {"label": "720p HD", "height": 720, "downloadUrl": f"/api/download?url={encoded_url}&quality=720p"},
                    {"label": "480p", "height": 480, "downloadUrl": f"/api/download?url={encoded_url}&quality=480p"},
                    {"label": "360p", "height": 360, "downloadUrl": f"/api/download?url={encoded_url}&quality=360p"}
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
                    "downloadUrl": qualities[0]["downloadUrl"],
                    "qualities": qualities,
                    "engine": "youtube-oembed"
                }
    except Exception as e:
        logger.warning("oEmbed fallback error: %s", e)
    return None


# ─── Generic yt-dlp Extraction Engine ────────────────────────────────────────
def extract_ytdlp(url: str, platform: str, cookie_file: Optional[str] = None) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file

    if platform == "YouTube":
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "tv"]}}
    elif platform == "Instagram":
        opts["http_headers"]["User-Agent"] = MOBILE_UA
        opts["http_headers"]["X-IG-App-ID"] = "936619743392459"
        opts["http_headers"]["Referer"] = "https://www.instagram.com/"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info


# ─── Lifespan & FastAPI Setup ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    has_cookies = bool(os.environ.get(COOKIES_ENV))
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


# ─── /api/info Endpoint ──────────────────────────────────────────────────────
@app.get("/api/info")
async def get_info(url: str = Query(...)):
    url = url.strip()
    if not url:
        return JSONResponse(status_code=400, content={"error": "URL is required"})

    platform = detect_platform(url)
    logger.info("Handling /api/info for platform=%s, url=%s", platform, url[:80])

    # 1. Platform-Specific: TikTok (TikWM)
    if platform == "TikTok":
        tt_res = await extract_tiktok(url)
        if tt_res:
            return JSONResponse(content=tt_res)

    # 2. Universal yt-dlp Extraction
    cf = _write_cookie_file()
    ytdlp_error = None
    try:
        info = await asyncio.to_thread(extract_ytdlp, url, platform, cf)
        title = info.get("title") or f"{platform} Video"
        thumbnail = info.get("thumbnail") or ""
        duration_sec = info.get("duration") or 0
        duration_str = _format_duration(duration_sec)
        safe_name = f"{_sanitize_title(title)}_{platform}.mp4"

        # Formats extraction
        formats = info.get("formats") or []
        heights = set()
        for f in formats:
            h = f.get("height")
            if isinstance(h, int) and h > 0:
                heights.add(h)

        std_heights = [144, 360, 480, 720, 1080]
        max_h = max(heights) if heights else 720
        usable_heights = sorted([h for h in std_heights if h <= max(max_h, 720)], reverse=True)
        if not usable_heights:
            usable_heights = [720, 480, 360]

        encoded_url = urllib.parse.quote(url)
        qualities = [
            {
                "label": f"{h}p {'Full HD' if h>=1080 else 'HD' if h>=720 else 'SD'}",
                "height": h,
                "downloadUrl": f"/api/download?url={encoded_url}&quality={h}p&filename={urllib.parse.quote(safe_name)}"
            }
            for h in usable_heights
        ]

        size_bytes = info.get("filesize") or info.get("filesize_approx") or 0
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes else "HD MP4"

        # Direct stream url if single format
        stream_url = info.get("url")
        rf = info.get("requested_formats")
        if rf and isinstance(rf, list) and len(rf) > 0:
            stream_url = rf[0].get("url") or stream_url

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
            "downloadUrl": qualities[0]["downloadUrl"],
            "qualities": qualities,
            "direct_stream_url": stream_url,
            "engine": "yt-dlp"
        })
    except Exception as e:
        ytdlp_error = str(e)
        logger.warning("yt-dlp extraction failed for %s: %s", platform, e)

    # 3. Fallback: YouTube oEmbed
    if platform == "YouTube":
        oe = await extract_youtube_oembed(url)
        if oe:
            return JSONResponse(content=oe)

    # 4. If all fail, return descriptive error
    err_lower = (ytdlp_error or "").lower()
    if "429" in err_lower or "too many" in err_lower:
        msg = "YouTube is rate-limiting server requests. Please try again shortly or use another video."
    elif "private" in err_lower or "login" in err_lower:
        msg = "This video is private or requires account login."
    elif "unavailable" in err_lower or "deleted" in err_lower:
        msg = "This video is unavailable or has been deleted."
    else:
        msg = f"Could not extract video from {platform}. Please ensure the link is public and valid."

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
    filename: Optional[str] = Query(None)
):
    target_url = url or videoUrl
    raw_stream_url = direct_url or src
    platform = detect_platform(target_url or "") if target_url else "Web"
    safe_name = filename or f"video_{platform}.mp4"

    # 1. If direct stream URL already provided (e.g. from TikWM or direct CDN)
    if raw_stream_url:
        logger.info("Handling direct stream URL for %s", safe_name)
        try:
            async def _proxy_direct():
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    async with client.stream("GET", raw_stream_url, headers={"User-Agent": BROWSER_UA}) as resp:
                        async for chunk in resp.aiter_bytes(65536):
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
            logger.warning("Proxy stream failed (%s), redirecting directly to CDN", e)
            return RedirectResponse(url=raw_stream_url)

    if not target_url:
        return JSONResponse(status_code=400, content={"error": "URL is required for download"})

    # 2. Check TikTok via TikWM if direct URL was not sent
    if platform == "TikTok":
        tt = await extract_tiktok(target_url)
        if tt and tt.get("direct_stream_url"):
            d_url = tt["direct_stream_url"]
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
                return RedirectResponse(url=d_url)

    # 3. Resolve stream URL via yt-dlp
    cf = _write_cookie_file()
    h = int(quality[:-1]) if quality.endswith("p") and quality[:-1].isdigit() else 720
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "format": f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}][ext=mp4]/best[height<={h}]/best",
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9"
        }
    }
    if cf:
        opts["cookiefile"] = cf
    if platform == "YouTube":
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "tv"]}}
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
