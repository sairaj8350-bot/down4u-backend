const express = require('express');
const cors = require('cors');
const axios = require('axios');
const path = require('path');
const { execFile, spawn } = require('child_process');
const fs = require('fs');

// Automated yt-dlp binary resolver for Windows & Linux (Render/Cloud)
function getYtDlpBinary() {
  const localExe = path.join(__dirname, 'yt-dlp.exe');
  const localLinux = path.join(__dirname, 'yt-dlp');
  
  if (process.platform === 'win32') {
    return fs.existsSync(localExe) ? localExe : 'yt-dlp.exe';
  }
  
  if (fs.existsSync(localLinux)) {
    try { fs.chmodSync(localLinux, '755'); } catch (e) {}
    return localLinux;
  }
  
  return 'yt-dlp';
}

let YTDLP_BIN = getYtDlpBinary();

const app = express();
const PORT = process.env.PORT || 5000;

// CORS — app.use(cors()) auto-handles OPTIONS preflight for ALL routes
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'OPTIONS', 'HEAD'],
  allowedHeaders: ['*'],
  exposedHeaders: ['Content-Disposition', 'Content-Length', 'Content-Type']
}));

// Root & Health Endpoints for Cloud Verification & Uptime Monitoring
app.get('/', (req, res) => {
  res.json({
    status: 'online',
    app: 'Down4U Pro Video Downloader API Engine',
    version: '2.7.1',
    ytdlpReady,
    timestamp: new Date().toISOString()
  });
});

app.get('/health', (req, res) => {
  res.json({ status: 'online', ytdlpReady, timestamp: new Date().toISOString() });
});

app.get('/api/health', (req, res) => {
  res.json({ status: 'online', ytdlpReady, timestamp: new Date().toISOString() });
});

app.use(express.static(__dirname));

// Domain whitelist
const DOMAIN_WHITELIST = [
  'youtube.com', 'youtu.be', 'youtube-nocookie.com',
  'instagram.com', 'facebook.com', 'fb.watch',
  'tiktok.com', 'vm.tiktok.com',
  'twitter.com', 'x.com', 't.co',
  'dailymotion.com', 'vimeo.com',
  'reddit.com', 'redd.it',
  'twitch.tv', 'clips.twitch.tv'
];

function detectPlatform(url) {
  if (url.includes('youtube.com') || url.includes('youtu.be')) return 'YouTube';
  if (url.includes('instagram.com')) return 'Instagram';
  if (url.includes('facebook.com') || url.includes('fb.watch')) return 'Facebook';
  if (url.includes('tiktok.com')) return 'TikTok';
  if (url.includes('twitter.com') || url.includes('x.com')) return 'Twitter';
  if (url.includes('dailymotion.com')) return 'Dailymotion';
  if (url.includes('vimeo.com')) return 'Vimeo';
  if (url.includes('reddit.com') || url.includes('redd.it')) return 'Reddit';
  if (url.includes('twitch.tv')) return 'Twitch';
  return 'Web';
}

let ytdlpReady = false;

function verifyYtDlp() {
  execFile(YTDLP_BIN, ['--version'], (err, stdout) => {
    if (err) {
      console.warn(`⚠️  yt-dlp check failed (${YTDLP_BIN}): ${err.message}`);
      if (process.platform !== 'win32' && !fs.existsSync(path.join(__dirname, 'yt-dlp'))) {
        const targetPath = path.join(__dirname, 'yt-dlp');
        const dlStream = fs.createWriteStream(targetPath);
        axios.get('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp', { responseType: 'stream' })
          .then(res => {
            res.data.pipe(dlStream);
            dlStream.on('finish', () => {
              try { fs.chmodSync(targetPath, '755'); } catch (e) {}
              YTDLP_BIN = targetPath;
              ytdlpReady = true;
            });
          })
          .catch(() => { ytdlpReady = false; });
      } else {
        ytdlpReady = false;
      }
    } else {
      console.log(`✅ yt-dlp ready: v${stdout.trim()} [${YTDLP_BIN}]`);
      ytdlpReady = true;
    }
  });
}

verifyYtDlp();

// Helper: extract quality options from formats
function extractQualities(info, platform, originalUrl, filename) {
  const standardHeights = [144, 360, 480, 720, 1080];
  
  let maxHeight = 1080;
  if (info.formats && info.formats.length > 0) {
    const heights = info.formats.map(f => f.height).filter(h => typeof h === 'number' && h > 0);
    if (heights.length > 0) {
      maxHeight = Math.max(...heights);
    }
  }

  const allowedHeights = standardHeights.filter(h => h <= Math.max(maxHeight, 720));
  const finalHeights = allowedHeights.length > 0 ? allowedHeights : standardHeights;

  return finalHeights.map(h => {
    let label = `${h}p`;
    if (h >= 1080) label = '1080p Full HD';
    else if (h >= 720) label = '720p HD';

    const qFilename = filename.replace(/\.mp4$/i, `_${h}p.mp4`);
    let formatSpec = `best[height<=${h}][ext=mp4]/best[height<=${h}]/18/22/best`;
    if (h === 360) formatSpec = `18/best[height<=360]/best`;
    else if (h === 720) formatSpec = `22/best[height<=720]/best`;

    return {
      label,
      height: h,
      formatId: formatSpec,
      downloadUrl: `/api/stream?videoUrl=${encodeURIComponent(originalUrl)}&format=${encodeURIComponent(formatSpec)}&platform=${encodeURIComponent(platform)}&filename=${encodeURIComponent(qFilename)}`
    };
  });
}

// ─── 4-Layer Zero-Block Resilient Extractor Engine ──────────────────────────
function extractVideoInfo(url, callback) {
  const platform = detectPlatform(url);

  // Common anti-block flags
  const baseFlags = [
    '-j',
    '--no-warnings',
    '--no-playlist',
    '--socket-timeout', '20',
    '--no-check-certificates',
    '--geo-bypass'
  ];

  // Layer 1: Android Client (Bypasses YouTube bot check & 403 stream error)
  const argsLayer1 = [...baseFlags];
  if (platform === 'YouTube') {
    argsLayer1.push('--extractor-args', 'youtube:player_client=android');
  } else if (platform === 'Instagram') {
    argsLayer1.push('--extractor-args', 'instagram:app_version=312.0.0.34.111');
    argsLayer1.push('--add-header', 'User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1');
    argsLayer1.push('--add-header', 'Referer:https://www.instagram.com/');
  } else if (platform === 'TikTok') {
    argsLayer1.push('--extractor-args', 'tiktok:app_version=34.1.2');
  }
  argsLayer1.push(url);

  execFile(YTDLP_BIN, argsLayer1, { maxBuffer: 1024 * 1024 * 30, timeout: 35000 }, (err1, stdout1, stderr1) => {
    if (!err1 && stdout1) {
      return callback(null, stdout1, platform);
    }

    console.warn(`[RETRY Layer 2] Primary extraction failed for ${platform}, trying Layer 2 (Android Creator)...`);
    
    // Layer 2: Android Creator (Alternative player client)
    const argsLayer2 = [...baseFlags];
    if (platform === 'YouTube') {
      argsLayer2.push('--extractor-args', 'youtube:player_client=android_creator,web_creator');
    } else if (platform === 'Instagram') {
      argsLayer2.push('--add-header', 'User-Agent:Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36');
      argsLayer2.push('--add-header', 'X-IG-App-ID:936619743392459');
    }
    argsLayer2.push(url);

    execFile(YTDLP_BIN, argsLayer2, { maxBuffer: 1024 * 1024 * 30, timeout: 35000 }, (err2, stdout2, stderr2) => {
      if (!err2 && stdout2) {
        return callback(null, stdout2, platform);
      }

      console.warn(`[RETRY Layer 3] Layer 2 failed for ${platform}, trying Layer 3 (Clean Generic Fallback)...`);

      // Layer 3: Clean Fallback
      const argsLayer3 = [
        '-j',
        '--no-warnings',
        '--no-playlist',
        '--socket-timeout', '25',
        '--no-check-certificates',
        url
      ];

      execFile(YTDLP_BIN, argsLayer3, { maxBuffer: 1024 * 1024 * 30, timeout: 40000 }, (err3, stdout3, stderr3) => {
        if (!err3 && stdout3) {
          return callback(null, stdout3, platform);
        }
        return callback(err3 || err2 || err1, null, platform, stderr3 || stderr2 || stderr1);
      });
    });
  });
}

// ─── /api/info — Fetch Video Metadata ────────────────────────────────────────
app.get('/api/info', (req, res) => {
  const { url } = req.query;
  if (!url) return res.status(400).json({ error: 'URL is required' });

  let parsedUrl;
  try {
    parsedUrl = new URL(url);
  } catch (_) {
    return res.status(400).json({ error: 'Invalid URL format. Please enter a valid web address.' });
  }

  const hostname = parsedUrl.hostname.replace('www.', '').toLowerCase();
  if (!DOMAIN_WHITELIST.some(d => hostname.includes(d))) {
    return res.status(400).json({
      error: 'Domain not supported. Supported: YouTube, Instagram, TikTok, Facebook, Twitter, Vimeo, Reddit'
    });
  }

  console.log(`[INFO] Extracting metadata for → ${url}`);

  extractVideoInfo(url, (err, stdout, platform, stderr) => {
    if (err || !stdout) {
      console.error('[yt-dlp ERROR]', err ? err.message : 'No output', stderr || '');
      const errStr = ((err ? err.message : '') + ' ' + (stderr || '')).toLowerCase();
      if (errStr.includes('private video') || errStr.includes('login required')) {
        return res.status(400).json({ error: 'This media is private or requires login.' });
      }
      if (errStr.includes('incomplete') || errStr.includes('not a valid url') || errStr.includes('invalid url') || errStr.includes('truncated')) {
        return res.status(400).json({ error: 'Invalid or incomplete video link. Please copy the full link.' });
      }
      if (errStr.includes('removed') || errStr.includes('unavailable') || errStr.includes('does not exist')) {
        return res.status(400).json({ error: 'This video is unavailable or has been deleted.' });
      }
      return res.status(400).json({ error: 'Could not extract video. Please ensure the link is public and valid.' });
    }

    let info;
    try {
      info = JSON.parse(stdout);
    } catch (parseErr) {
      return res.status(500).json({ error: 'Unexpected response from video source.' });
    }

    const title = info.title || 'Media Ready';
    const thumbnail = info.thumbnail || '';
    const durationSec = info.duration || 0;
    const minutes = Math.floor(durationSec / 60);
    const seconds = Math.floor(durationSec % 60);
    const duration = durationSec > 0 ? `${minutes}:${seconds.toString().padStart(2, '0')}` : '';
    const sizeBytes = info.filesize || info.filesize_approx || 0;

    const ext = (info.ext || '').toLowerCase();
    const isImage = ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext) ||
                    info._type === 'image' ||
                    (!durationSec && !info.formats?.some(f => f.vcodec && f.vcodec !== 'none'));

    const mediaType = isImage ? 'image' : 'video';
    const fileExt = isImage ? (['jpg', 'jpeg', 'png', 'webp'].includes(ext) ? ext : 'jpg') : 'mp4';
    const safeTitle = (info.title || (isImage ? 'photo' : 'video')).replace(/[^a-z0-9]/gi, '_').substring(0, 40);
    const filename = `${safeTitle}_${platform}.${fileExt}`;
    const size = sizeBytes ? `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB` : (isImage ? 'HD Photo' : 'HD MP4');

    // Only extract video quality pills if it is a video (never for photos/images)
    const qualities = isImage ? [] : extractQualities(info, platform, url, filename);
    const defaultFormat = qualities.length > 0 ? qualities[qualities.length - 1].formatId : '18/22/best[ext=mp4]/best';

    const directImageUrl = isImage ? (info.url || info.thumbnail) : null;
    const defaultDownloadUrl = directImageUrl
      ? `/api/stream?src=${encodeURIComponent(directImageUrl)}&platform=${encodeURIComponent(platform)}&filename=${encodeURIComponent(filename)}`
      : `/api/stream?videoUrl=${encodeURIComponent(url)}&format=${encodeURIComponent(defaultFormat)}&platform=${encodeURIComponent(platform)}&filename=${encodeURIComponent(filename)}`;

    console.log(`[INFO] Success: "${title}" | Type: ${mediaType} | Qualities: ${qualities.map(q => q.label).join(', ') || (isImage ? 'None (Photo)' : 'Best')}`);

    return res.json({
      success: true,
      platform,
      title,
      duration,
      thumbnail,
      size,
      mediaType,
      fileExt,
      filename,
      downloadUrl: defaultDownloadUrl,
      qualities
    });
  });
});

// ─── /api/stream — Reliable Video/Photo Stream Download ──────────────────────
app.get('/api/stream', (req, res) => {
  const { videoUrl, format, src, filename: reqFilename, platform } = req.query;

  const filename = reqFilename
    ? decodeURIComponent(reqFilename)
    : `Down4U_${platform || 'Video'}_${Date.now()}.mp4`;

  // Determine content type from filename extension
  const extMatch = filename.match(/\.(\w+)$/i);
  const ext = extMatch ? extMatch[1].toLowerCase() : 'mp4';
  const contentTypeMap = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif', mp4: 'video/mp4' };
  const contentType = contentTypeMap[ext] || 'video/mp4';

  // If videoUrl is provided, stream directly via yt-dlp with cloud bypass
  if (videoUrl) {
    const targetUrl = decodeURIComponent(videoUrl);
    const formatSpec = format ? decodeURIComponent(format) : '18/22/best[ext=mp4]/best';

    console.log(`[STREAM yt-dlp] ${filename} | format: ${formatSpec}`);

    const args = [
      '--extractor-args', 'youtube:player_client=android',
      '-f', formatSpec,
      '--no-warnings',
      '--no-playlist',
      '--no-check-certificates',
      '--geo-bypass',
      '-o', '-',
      targetUrl
    ];

    const child = spawn(YTDLP_BIN, args);
    let hasSentData = false;

    child.stdout.on('data', (chunk) => {
      if (!hasSentData) {
        hasSentData = true;
        res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
        res.setHeader('Content-Type', contentType);
        res.setHeader('Accept-Ranges', 'bytes');
      }
      res.write(chunk);
    });

    child.stdout.on('end', () => {
      if (hasSentData) {
        res.end();
      } else {
        console.warn(`[STREAM FALLBACK] yt-dlp stdout yielded 0 bytes. Fetching direct CDN URL...`);
        execFile(YTDLP_BIN, ['--extractor-args', 'youtube:player_client=android', '-f', formatSpec, '-g', targetUrl], (gErr, gStdout) => {
          if (!gErr && gStdout && gStdout.trim()) {
            const directUrl = gStdout.trim().split('\n')[0];
            console.log(`[STREAM FALLBACK] Streaming via direct URL: ${directUrl.substring(0, 60)}...`);
            axios({
              url: directUrl,
              method: 'GET',
              responseType: 'stream',
              headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' }
            }).then(resp => {
              if (!res.headersSent) {
                res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
                res.setHeader('Content-Type', contentType);
                if (resp.headers['content-length']) res.setHeader('Content-Length', resp.headers['content-length']);
              }
              resp.data.pipe(res);
              req.on('close', () => resp.data.destroy());
            }).catch(e => {
              console.error('[STREAM FALLBACK ERROR]', e.message);
              if (!res.headersSent) res.status(500).json({ error: 'Stream failed' });
            });
          } else {
            if (!res.headersSent) res.status(500).json({ error: 'Failed to stream video data' });
          }
        });
      }
    });

    child.stderr.on('data', (d) => {
      const str = d.toString();
      if (str.includes('ERROR:')) console.error('[yt-dlp stream error]', str);
    });

    req.on('close', () => {
      child.kill('SIGKILL');
    });

    child.on('error', (err) => {
      console.error('[Process Error]', err.message);
      if (!res.headersSent) res.status(500).json({ error: 'Stream process failed' });
    });

    return;
  }

  // Fallback: Axios direct stream if src is provided
  if (src) {
    const decodedSrc = decodeURIComponent(src);
    axios({
      url: decodedSrc,
      method: 'GET',
      responseType: 'stream',
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
      },
      timeout: 0
    }).then(response => {
      if (response.headers['content-length']) {
        res.setHeader('Content-Length', response.headers['content-length']);
      }
      res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
      res.setHeader('Content-Type', contentType);
      response.data.pipe(res);
      req.on('close', () => response.data.destroy());
    }).catch(err => {
      console.error('[Axios Stream Error]', err.message);
      if (!res.headersSent) res.status(500).json({ error: 'Stream failed' });
    });
    return;
  }

  res.status(400).json({ error: 'Missing videoUrl or src parameter' });
});

// ─── Self Keep-Alive Anti-Sleep Engine ──────────────────────────────────────
const SELF_URL = process.env.RENDER_EXTERNAL_URL || 'https://down4u-backend.onrender.com';
setInterval(() => {
  if (SELF_URL) {
    axios.get(`${SELF_URL}/api/health`, { timeout: 10000 })
      .then(() => console.log(`[Keep-Alive Ping] Sent heartbeat to ${SELF_URL}`))
      .catch(() => {});
  }
}, 10 * 60 * 1000); // Every 10 mins

// ─── 404 Fallback — Standard middleware (NO wildcard '*' routes) ─────────────
app.use((req, res) => {
  res.status(404).json({ error: 'Route not found', path: req.originalUrl });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`\n🚀 Down4U v2.7.1 Production Server running on http://localhost:${PORT} (Bound to 0.0.0.0)\n`);
});
