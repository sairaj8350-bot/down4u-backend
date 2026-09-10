"""
AnyVideoDownloader - Python FastAPI Backend
Dual-Engine Fallback: yt-dlp (iOS/Android spoofing + cookies) -> cobalt.tools
"""

import os, re, json, asyncio, logging, tempfile, traceback
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import yt_dlp
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("avd")

COOKIES_ENV = "YT_COOKIES"
COBALT_API  = "https://api.cobalt.tools"
BROWSER_UA  = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
PLAYER_CLIENTS = ["ios", "android", "mweb", "web"]

def _write_cookie_file():
    raw = os.environ.get(COOKIES_ENV, "").strip()
    if not raw: return None
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    if not raw.startswith("# Netscape"): tmp.write("# Netscape HTTP Cookie File\n")
    tmp.write(raw); tmp.flush(); tmp.close()
    logger.info("Cookie file: %s", tmp.name)
    return tmp.name

def _base_opts(cookie_file):
    o = {"quiet": True, "no_warnings": True, "noplaylist": True,
         "http_headers": {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}}
    if cookie_file: o["cookiefile"] = cookie_file
    return o

def _extract(url, cookie_file):
    last = None
    for c in PLAYER_CLIENTS:
        opts = _base_opts(cookie_file)
        opts["extractor_args"] = {"youtube": {"player_client": [c]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                logger.info("yt-dlp OK client=%s", c); return info
        except Exception as e:
            logger.warning("yt-dlp client=%s fail: %s", c, e); last = e
    raise last

def _err(exc):
    m = str(exc).lower()
    if "429" in m or "too many" in m: return "YT_IP_BLOCKED","YouTube blocking cloud IP (429). Add cookies via YT_COOKIES env-var."
    if "403" in m or "forbidden" in m: return "YT_FORBIDDEN","Access forbidden (403). Age-restricted or region-locked."
    if "private" in m: return "YT_PRIVATE","Video is private."
    if "unavailable" in m: return "YT_UNAVAILABLE","Video unavailable or deleted."
    if "sign in" in m or "login" in m: return "YT_LOGIN_REQUIRED","Login required. Set YT_COOKIES env-var."
    return "YTDLP_ERROR", str(exc)

async def _cobalt(url, quality="1080"):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(COBALT_API+"/",
            json={"url":url,"videoQuality":quality,"audioFormat":"mp3","filenameStyle":"pretty","downloadMode":"auto"},
            headers={"Content-Type":"application/json","Accept":"application/json"})
        d = r.json(); s = d.get("status")
        if s in ("redirect","stream","tunnel"): return d.get("url") or d.get("u")
        if s == "picker": return d["picker"][0]["url"]
        raise RuntimeError(f"Cobalt status={s}")

@asynccontextmanager
async def lifespan(app):
    logger.info("AnyVideoDownloader backend started. Cookies=%s", bool(os.environ.get(COOKIES_ENV)))
    yield

app = FastAPI(title="AnyVideoDownloader API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
async def health():
    return {"status":"ok","engine":"yt-dlp+cobalt","cookies":bool(os.environ.get(COOKIES_ENV))}

@app.get("/api/info")
async def get_info(url: str = Query(...)):
    cf = _write_cookie_file(); ytdlp_err = None
    try:
        info = await asyncio.to_thread(_extract, url, cf)
        qmap = {}
        for f in info.get("formats",[]):
            if f.get("vcodec")=="none" or not f.get("height"): continue
            lbl = f"{f['height']}p"
            if lbl not in qmap or f.get("acodec")!="none":
                qmap[lbl] = {"format_id":f["format_id"],"ext":f.get("ext","mp4"),
                             "filesize":f.get("filesize") or f.get("filesize_approx"),
                             "has_audio":f.get("acodec")!="none"}
        quals = sorted(qmap.keys(), key=lambda q:int(q[:-1]), reverse=True)
        return {"status":"success","engine":"yt-dlp","title":info.get("title","Unknown"),
                "thumbnail":info.get("thumbnail"),"duration":info.get("duration"),
                "uploader":info.get("uploader"),"view_count":info.get("view_count"),
                "qualities":quals,"quality_details":qmap}
    except Exception as e:
        code,msg = _err(e); ytdlp_err={"code":code,"message":msg}
        logger.warning("Engine1 fail [%s]: %s", code, e)
    try:
        cu = await _cobalt(url)
        return {"status":"success","engine":"cobalt","title":"Video (via Cobalt)","thumbnail":None,
                "duration":None,"uploader":None,"view_count":None,
                "qualities":["1080p","720p","480p","360p"],"quality_details":{},"_cobalt_url":cu}
    except Exception as e2:
        logger.error("Engine2 fail: %s", e2)
    return JSONResponse(502,{"status":"error","primary_error":ytdlp_err,
        "fallback_error":"COBALT_FAILED","message":"Both engines failed. Set YT_COOKIES and retry."})

@app.get("/api/download")
async def download(url: str = Query(...), quality: str = Query("720p"), format_id: Optional[str] = Query(None)):
    cf = _write_cookie_file(); ytdlp_err = None
    try:
        h = int(quality[:-1]) if quality.endswith("p") else 720
        last = None; stream_url = None
        for c in PLAYER_CLIENTS:
            opts = _base_opts(cf)
            opts["extractor_args"] = {"youtube": {"player_client": [c]}}
            opts["format"] = format_id if format_id else (
                f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best")
            opts["merge_output_format"] = "mp4"
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    rf = info.get("requested_formats") or [info]
                    stream_url = rf[0].get("url") if rf else info.get("url")
                    if stream_url: logger.info("Stream URL OK client=%s", c); break
            except Exception as e: logger.warning("DL client=%s fail: %s", c, e); last = e
        if stream_url:
            fname = f"{quality}_video.mp4"
            async def _proxy():
                async with httpx.AsyncClient(timeout=None, follow_redirects=True) as hc:
                    async with hc.stream("GET", stream_url, headers={"User-Agent":BROWSER_UA}) as r:
                        async for chunk in r.aiter_bytes(65536): yield chunk
            return StreamingResponse(_proxy(), media_type="video/mp4",
                headers={"Content-Disposition":f'attachment; filename="{fname}"',"X-Engine":"yt-dlp"})
        if last: raise last
    except Exception as e:
        code,msg = _err(e); ytdlp_err={"code":code,"message":msg}
        logger.warning("DL Engine1 fail [%s]: %s", code, e)
    try:
        q = quality[:-1] if quality.endswith("p") else "720"
        cu = await _cobalt(url, q)
        async def _cs():
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as hc:
                async with hc.stream("GET", cu) as r:
                    async for chunk in r.aiter_bytes(65536): yield chunk
        return StreamingResponse(_cs(), media_type="video/mp4",
            headers={"Content-Disposition":'attachment; filename="video.mp4"',"X-Engine":"cobalt"})
    except Exception as e2:
        logger.error("DL Engine2 fail: %s", e2)
    return JSONResponse(502,{"status":"error","primary_error":ytdlp_err,
        "fallback_error":"COBALT_FAILED","message":"Both engines failed. Set YT_COOKIES and retry."})

@app.get("/api/formats")
async def formats(url: str = Query(...)):
    cf = _write_cookie_file()
    try:
        info = await asyncio.to_thread(_extract, url, cf)
        fs = [{"format_id":f.get("format_id"),"ext":f.get("ext"),"height":f.get("height"),
               "vcodec":f.get("vcodec"),"acodec":f.get("acodec"),
               "filesize":f.get("filesize") or f.get("filesize_approx")}
              for f in info.get("formats",[])]
        return {"status":"success","formats":fs}
    except Exception as e:
        code,msg = _err(e)
        return JSONResponse(502,{"status":"error","code":code,"message":msg})
