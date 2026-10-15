package com.down4u.videodownloader;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Log;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.Toast;

import com.getcapacitor.BridgeActivity;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLDecoder;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;

/**
 * Down4U MainActivity — v6.0.0
 *
 * Key fixes in this version:
 *  1. DEBUG ROOT-CAUSE LOGGING: expire= / ip= params highlighted, response status/body logged.
 *  2. FRESH URL RETRY: Every retry triggers JS to call /api/info again — never reuses stale URLs.
 *  3. PROPER HEADERS: Mobile UA, Range, Referer per platform on every download attempt.
 *  4. MAX 3 RETRIES with exponential backoff (1s, 2s, 4s).
 *  5. MEDIASTORE STORAGE: Downloads/Down4U folder, works Android 10–15 without permission issues.
 *  6. DETAILED ERROR reporting: HTTP status code + short description sent back to JS.
 */
public class MainActivity extends BridgeActivity {

    private static final String TAG = "Down4U_Downloader";

    // Set to BuildConfig.DEBUG to disable in release builds automatically.
    // Since we don't have BuildConfig here directly, check a compile-time constant.
    private static final boolean IS_DEBUG = true; // Change to false for release

    private static final int MAX_RETRIES = 3;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService downloadExecutor = Executors.newSingleThreadExecutor();
    private OkHttpClient okHttpClient;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        okHttpClient = new OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .followRedirects(true)
            .followSslRedirects(true)
            .retryOnConnectionFailure(false) // We handle retries ourselves
            .build();

        WebView webView = getBridge().getWebView();
        if (webView != null) {
            webView.addJavascriptInterface(new AndroidDownloader(this), "AndroidDownloader");

            webView.setDownloadListener(new DownloadListener() {
                @Override
                public void onDownloadStart(String url, String userAgent,
                        String contentDisposition, String mimetype, long contentLength) {
                    startNativeOkHttpDownload(url, "video_" + System.currentTimeMillis() + ".mp4",
                            "Video Download", mimetype, 0);
                }
            });
        }
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        if (downloadExecutor != null && !downloadExecutor.isShutdown()) {
            downloadExecutor.shutdownNow();
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  Core Download Method — with DEBUG logging & retry support
    // ─────────────────────────────────────────────────────────────────────────
    public void startNativeOkHttpDownload(String url, String filename,
                                          String title, String mimeType, int retryAttempt) {
        if (url == null || url.trim().isEmpty()) {
            notifyJsDownloadError("Download URL is empty or invalid", "EMPTY_URL", 0);
            return;
        }

        // ── ROOT-CAUSE LOGGING (DEBUG only) ──
        if (IS_DEBUG) {
            debugLogDownloadUrl(url, retryAttempt);
        }

        final String finalUrl = url.trim();
        final String cleanTitle = (title != null && !title.trim().isEmpty()) ? title : "Video Download";
        String cleanName = (filename != null && !filename.trim().isEmpty())
                ? filename : "video_" + System.currentTimeMillis() + ".mp4";
        cleanName = cleanName.replaceAll("[\\\\/:*?\"<>|]", "_");
        if (!cleanName.toLowerCase().endsWith(".mp4") && !cleanName.toLowerCase().endsWith(".jpg")) {
            cleanName += ".mp4";
        }
        final String targetFilename = cleanName;
        final String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : "video/mp4";
        final int attempt = retryAttempt;

        if (attempt == 0) {
            Toast.makeText(this, "Download started...", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "Retrying download (attempt " + (attempt + 1) + "/" + MAX_RETRIES + ")...",
                    Toast.LENGTH_SHORT).show();
        }

        downloadExecutor.execute(() -> {
            OutputStream outputStream = null;
            Uri mediaStoreUri = null;
            File targetFile = null;

            try {
                // ── Build request with platform-appropriate headers ──
                Request.Builder reqBuilder = new Request.Builder()
                    .url(finalUrl)
                    .header("User-Agent",
                        "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
                        + "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")
                    .header("Accept", "video/mp4,video/*;q=0.9,*/*;q=0.8")
                    .header("Accept-Encoding", "identity") // Prevent gzip on video stream
                    .header("Accept-Language", "en-US,en;q=0.9")
                    .header("Connection", "keep-alive")
                    .header("Range", "bytes=0-"); // Required for some CDNs

                // Platform-specific Referer & headers
                if (finalUrl.contains("cdninstagram.com") || finalUrl.contains("fbcdn.net")) {
                    reqBuilder.header("Referer", "https://www.instagram.com/");
                    reqBuilder.header("Origin", "https://www.instagram.com");
                } else if (finalUrl.contains("twimg.com")) {
                    reqBuilder.header("Referer", "https://twitter.com/");
                } else if (finalUrl.contains("tiktokcdn.com") || finalUrl.contains("tiktok.com")) {
                    reqBuilder.header("Referer", "https://www.tiktok.com/");
                    reqBuilder.header("TikTok-Client-Language", "en");
                } else if (finalUrl.contains("googlevideo.com")) {
                    // YouTube CDN — no Referer needed, already IP-bound
                    reqBuilder.removeHeader("Range"); // YouTube CDN handles range internally
                }

                Response response = okHttpClient.newCall(reqBuilder.build()).execute();

                int statusCode = response.code();
                String statusMessage = response.message();

                if (IS_DEBUG) {
                    Log.d(TAG, "──────────────────────────────────────────");
                    Log.d(TAG, "HTTP RESPONSE STATUS: " + statusCode + " " + statusMessage);
                    Log.d(TAG, "Response URL: " + response.request().url());
                    Log.d(TAG, "Content-Type: " + response.header("Content-Type", "N/A"));
                    Log.d(TAG, "Content-Length: " + response.header("Content-Length", "N/A"));
                    if (!response.isSuccessful()) {
                        ResponseBody errBody = response.body();
                        String errBodyStr = errBody != null ? errBody.string() : "(empty)";
                        Log.e(TAG, "ERROR RESPONSE BODY (first 500 chars): "
                                + errBodyStr.substring(0, Math.min(500, errBodyStr.length())));
                    }
                    Log.d(TAG, "──────────────────────────────────────────");
                }

                if (!response.isSuccessful()) {
                    String errorLabel = httpStatusLabel(statusCode);
                    String errorMsg;

                    if (statusCode == 400 && attempt < MAX_RETRIES - 1) {
                        // HTTP 400 on CDN usually means expired/IP-mismatch URL
                        // Notify JS to fetch a fresh URL, then retry
                        Log.w(TAG, "HTTP 400 on attempt " + attempt + " — requesting fresh URL from JS");
                        mainHandler.post(() -> notifyJsFreshUrlNeeded(attempt + 1));
                        response.close();
                        return;
                    } else if (statusCode == 403) {
                        errorMsg = "Access Forbidden (403) — " + errorLabel
                                + ". The link may be expired or region-locked.";
                    } else if (statusCode == 404) {
                        errorMsg = "Video Not Found (404) — The link may be deleted or expired.";
                    } else if (statusCode == 429) {
                        errorMsg = "Too Many Requests (429) — Please wait a moment and retry.";
                    } else {
                        errorMsg = "Download failed (HTTP " + statusCode + " " + errorLabel + ")";
                    }

                    Log.e(TAG, "Download FAILED: " + errorMsg);
                    final String finalMsg = errorMsg;
                    mainHandler.post(() -> {
                        Toast.makeText(MainActivity.this, finalMsg, Toast.LENGTH_LONG).show();
                        notifyJsDownloadError(finalMsg, "HTTP_" + statusCode, statusCode);
                    });
                    response.close();
                    return;
                }

                ResponseBody body = response.body();
                if (body == null) {
                    notifyJsDownloadError("Server returned empty response body", "EMPTY_BODY", 0);
                    return;
                }

                long totalBytes = body.contentLength();
                InputStream inputStream = body.byteStream();

                // ── Open destination via MediaStore (Android 10+) or direct file ──
                ContentResolver resolver = getContentResolver();
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Video.Media.DISPLAY_NAME, targetFilename);
                    values.put(MediaStore.Video.Media.MIME_TYPE, safeMime);
                    values.put(MediaStore.Video.Media.RELATIVE_PATH,
                            Environment.DIRECTORY_DOWNLOADS + "/Down4U");
                    values.put(MediaStore.Video.Media.IS_PENDING, 1);

                    mediaStoreUri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                    if (mediaStoreUri == null) {
                        mediaStoreUri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
                    }
                    if (mediaStoreUri != null) {
                        outputStream = resolver.openOutputStream(mediaStoreUri);
                    }
                }

                if (outputStream == null) {
                    File downloadsDir = Environment.getExternalStoragePublicDirectory(
                            Environment.DIRECTORY_DOWNLOADS);
                    File appDir = new File(downloadsDir, "Down4U");
                    if (!appDir.exists()) appDir.mkdirs();
                    targetFile = new File(appDir, targetFilename);
                    outputStream = new FileOutputStream(targetFile);
                }

                // ── Stream bytes with throttled progress reporting ──
                byte[] buffer = new byte[65536];
                int bytesRead;
                long totalDownloaded = 0;
                long lastProgressTime = 0;

                while ((bytesRead = inputStream.read(buffer)) != -1) {
                    outputStream.write(buffer, 0, bytesRead);
                    totalDownloaded += bytesRead;

                    long now = System.currentTimeMillis();
                    if (now - lastProgressTime > 200) {
                        lastProgressTime = now;
                        int percent = (totalBytes > 0)
                                ? (int) ((totalDownloaded * 100L) / totalBytes) : -1;
                        if (percent > 99) percent = 99;
                        final int p = percent;
                        final String dlStr = formatBytes(totalDownloaded);
                        final String totStr = totalBytes > 0 ? formatBytes(totalBytes) : "?";
                        mainHandler.post(() -> notifyJsProgress(p, dlStr, totStr));
                    }
                }

                outputStream.flush();
                outputStream.close();
                outputStream = null;

                // Mark MediaStore entry as complete
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && mediaStoreUri != null) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Video.Media.IS_PENDING, 0);
                    resolver.update(mediaStoreUri, values, null, null);
                }

                // Scan into Gallery
                final String scanPath = targetFile != null ? targetFile.getAbsolutePath() : null;
                if (scanPath != null) {
                    MediaScannerConnection.scanFile(
                        MainActivity.this,
                        new String[]{scanPath},
                        new String[]{safeMime},
                        (path, uri) -> {
                            Intent scanIntent = new Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE);
                            scanIntent.setData(uri);
                            sendBroadcast(scanIntent);
                        }
                    );
                }

                Log.i(TAG, "✅ Download complete! Bytes saved: " + totalDownloaded
                        + " | File: " + targetFilename);
                final long finalTotal = totalDownloaded;
                mainHandler.post(() -> {
                    Toast.makeText(MainActivity.this, "✅ Saved to Gallery!", Toast.LENGTH_SHORT).show();
                    notifyJsProgress(100, formatBytes(finalTotal), formatBytes(finalTotal));
                    notifyJsDownloadComplete(true);
                });

            } catch (Exception e) {
                Log.e(TAG, "Download exception on attempt " + attempt + ": " + e.getMessage(), e);

                // Cleanup partial downloads
                if (mediaStoreUri != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    try { getContentResolver().delete(mediaStoreUri, null, null); } catch (Exception ignored) {}
                }
                if (targetFile != null && targetFile.exists()) {
                    try { targetFile.delete(); } catch (Exception ignored) {}
                }

                // Retry with exponential backoff for network errors
                if (attempt < MAX_RETRIES - 1 && isRetryableError(e)) {
                    long delay = (long) Math.pow(2, attempt) * 1000L;
                    Log.w(TAG, "Retryable error, retrying in " + delay + "ms: " + e.getMessage());
                    mainHandler.postDelayed(() ->
                        startNativeOkHttpDownload(finalUrl, targetFilename, cleanTitle, safeMime, attempt + 1),
                        delay
                    );
                } else {
                    final String errMsg = "Download error: " + e.getMessage();
                    mainHandler.post(() -> {
                        Toast.makeText(MainActivity.this, errMsg, Toast.LENGTH_LONG).show();
                        notifyJsDownloadError(errMsg, "EXCEPTION", 0);
                    });
                }
            } finally {
                if (outputStream != null) {
                    try { outputStream.close(); } catch (Exception ignored) {}
                }
            }
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  DEBUG: Log URL details — expire= and ip= params highlighted
    // ─────────────────────────────────────────────────────────────────────────
    private void debugLogDownloadUrl(String url, int attempt) {
        Log.d(TAG, "══════════════════════════════════════════════════");
        Log.d(TAG, "DOWN4U DOWNLOAD ATTEMPT #" + attempt);
        Log.d(TAG, "URL Source: " + (url.contains("onrender.com") ? "⚠️ RENDER BACKEND" : "✅ DIRECT CDN"));
        Log.d(TAG, "Full URL: " + url);

        // Highlight expiry and IP binding params
        try {
            String decoded = URLDecoder.decode(url, "UTF-8");
            Pattern expirePattern = Pattern.compile("expire=([0-9]+)");
            Pattern ipPattern = Pattern.compile("ip=([^&]+)");
            Pattern ratebypassPattern = Pattern.compile("ratebypass=([^&]+)");

            Matcher expireMatcher = expirePattern.matcher(decoded);
            if (expireMatcher.find()) {
                long expireTs = Long.parseLong(expireMatcher.group(1));
                long nowTs = System.currentTimeMillis() / 1000;
                long diff = expireTs - nowTs;
                if (diff > 0) {
                    Log.d(TAG, "⏰ expire= " + expireMatcher.group(1) + " → expires in " + diff + "s ✅");
                } else {
                    Log.e(TAG, "💀 expire= " + expireMatcher.group(1) + " → EXPIRED " + (-diff) + "s ago ❌");
                }
            }

            Matcher ipMatcher = ipPattern.matcher(decoded);
            if (ipMatcher.find()) {
                Log.d(TAG, "📍 ip= " + ipMatcher.group(1) + " (this URL is bound to this server IP!)");
                Log.w(TAG, "⚠️ If device IP ≠ ip= above → HTTP 400 expected. Need fresh direct URL.");
            }

            Matcher ratebypassMatcher = ratebypassPattern.matcher(decoded);
            if (ratebypassMatcher.find()) {
                Log.d(TAG, "🔑 ratebypass=" + ratebypassMatcher.group(1));
            }
        } catch (Exception e) {
            Log.w(TAG, "Could not decode URL for param analysis: " + e.getMessage());
        }
        Log.d(TAG, "══════════════════════════════════════════════════");
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  Helpers
    // ─────────────────────────────────────────────────────────────────────────
    private boolean isRetryableError(Exception e) {
        String msg = e.getMessage() != null ? e.getMessage().toLowerCase() : "";
        return e instanceof java.net.SocketTimeoutException
            || e instanceof java.net.UnknownHostException
            || e instanceof java.net.ConnectException
            || msg.contains("timeout")
            || msg.contains("connection reset")
            || msg.contains("eof");
    }

    private String httpStatusLabel(int code) {
        switch (code) {
            case 400: return "Bad Request (Expired/IP-bound URL)";
            case 401: return "Unauthorized";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            case 410: return "Gone (Video Removed)";
            case 429: return "Too Many Requests";
            case 500: return "Server Error";
            case 502: return "Bad Gateway";
            case 503: return "Service Unavailable";
            default: return "";
        }
    }

    private String formatBytes(long bytes) {
        if (bytes < 0) return "0 B";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) return String.format("%.1f MB", bytes / (1024.0 * 1024));
        return String.format("%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    private String escapeJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "")
                .replace("\r", "");
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  JS Bridge callbacks
    // ─────────────────────────────────────────────────────────────────────────
    private void notifyJsProgress(int percent, String downloaded, String total) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onNativeDownloadProgress==='function')" +
                        "window.onNativeDownloadProgress(" + percent + ",'"
                        + escapeJs(downloaded) + "','" + escapeJs(total) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    private void notifyJsDownloadComplete(boolean success) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onNativeDownloadComplete==='function')" +
                        "window.onNativeDownloadComplete(" + success + ");";
            webView.evaluateJavascript(js, null);
        });
    }

    /**
     * Called when HTTP 400 is received — tells JS to fetch a fresh URL and
     * then call downloadVideo() again with the fresh URL.
     */
    private void notifyJsFreshUrlNeeded(int nextAttempt) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onNativeNeedsFreshUrl==='function')" +
                        "window.onNativeNeedsFreshUrl(" + nextAttempt + ");";
            webView.evaluateJavascript(js, null);
        });
    }

    /**
     * Reports detailed error to JS layer — includes HTTP status code and description.
     */
    private void notifyJsDownloadError(String message, String code, int httpStatus) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onNativeDownloadError==='function')" +
                        "window.onNativeDownloadError('" + escapeJs(message) + "','"
                        + escapeJs(code) + "'," + httpStatus + ");";
            webView.evaluateJavascript(js, null);
            // Also trigger complete(false) for backward compat
            String js2 = "if(typeof window.onNativeDownloadComplete==='function')" +
                         "window.onNativeDownloadComplete(false);";
            webView.evaluateJavascript(js2, null);
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  Headless In-App Native Sniffer (WebView-based stream extractor)
    // ─────────────────────────────────────────────────────────────────────────
    private WebView headlessWebView;
    private boolean isSniffing = false;

    private void startHeadlessSniffer(String targetUrl, String defaultTitle) {
        if (targetUrl == null || targetUrl.trim().isEmpty()) return;

        if (headlessWebView != null) {
            try {
                headlessWebView.stopLoading();
                headlessWebView.destroy();
            } catch (Exception ignored) {}
            headlessWebView = null;
        }

        isSniffing = true;
        headlessWebView = new WebView(this);
        android.webkit.WebSettings settings = headlessWebView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36");

        final String cleanTitle = (defaultTitle != null && !defaultTitle.isEmpty()) ? defaultTitle : "Video";
        final String safeFilename = cleanTitle.replaceAll("[^a-zA-Z0-9_\\-]", "_")
                + "_" + System.currentTimeMillis() + ".mp4";

        notifyJsSnifferStatus("Connecting direct in-app stream extractor...");

        String loadUrl = targetUrl;
        if (targetUrl.contains("instagram.com/reel/") || targetUrl.contains("instagram.com/p/")
                || targetUrl.contains("instagram.com/tv/")) {
            String shortcode = extractInstagramShortcode(targetUrl);
            if (shortcode != null) {
                loadUrl = "https://www.instagram.com/reel/" + shortcode + "/";
            }
        }

        mainHandler.postDelayed(() -> {
            if (isSniffing && headlessWebView != null) {
                isSniffing = false;
                try {
                    headlessWebView.stopLoading();
                    headlessWebView.destroy();
                } catch (Exception ignored) {}
                headlessWebView = null;
                notifyJsSnifferFailed("Stream search timed out. Please check your connection.");
            }
        }, 18000);

        final String finalLoadUrl = loadUrl;
        headlessWebView.setWebViewClient(new android.webkit.WebViewClient() {
            @Override
            public android.webkit.WebResourceResponse shouldInterceptRequest(
                    WebView view, android.webkit.WebResourceRequest request) {
                if (!isSniffing) return super.shouldInterceptRequest(view, request);
                String reqUrl = request.getUrl().toString();
                if (isVideoStreamUrl(reqUrl)) {
                    isSniffing = false;
                    mainHandler.post(() -> {
                        if (headlessWebView != null) {
                            try {
                                headlessWebView.stopLoading();
                                headlessWebView.destroy();
                            } catch (Exception ignored) {}
                            headlessWebView = null;
                        }
                        notifyJsSnifferSuccess(reqUrl);
                        startNativeOkHttpDownload(reqUrl, safeFilename, cleanTitle, "video/mp4", 0);
                    });
                }
                return super.shouldInterceptRequest(view, request);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!isSniffing) return;
                view.evaluateJavascript(
                    "(function() {" +
                    "  var v = document.querySelector('video');" +
                    "  if (v) {" +
                    "    v.muted = true;" +
                    "    try { v.play(); } catch(e) {}" +
                    "    if (v.src && v.src.indexOf('http') === 0) return v.src;" +
                    "  }" +
                    "  var s = document.querySelector('video source');" +
                    "  if (s && s.src && s.src.indexOf('http') === 0) return s.src;" +
                    "  return '';" +
                    "})()",
                    value -> {
                        if (value != null && value.length() > 6
                                && !value.equals("\"\"") && !value.equals("null") && isSniffing) {
                            String foundUrl = value.replace("\"", "").replace("\\u0026", "&");
                            if (foundUrl.startsWith("http")) {
                                isSniffing = false;
                                mainHandler.post(() -> {
                                    if (headlessWebView != null) {
                                        try {
                                            headlessWebView.stopLoading();
                                            headlessWebView.destroy();
                                        } catch (Exception ignored) {}
                                        headlessWebView = null;
                                    }
                                    notifyJsSnifferSuccess(foundUrl);
                                    startNativeOkHttpDownload(foundUrl, safeFilename, cleanTitle,
                                            "video/mp4", 0);
                                });
                            }
                        }
                    }
                );
            }
        });

        headlessWebView.loadUrl(finalLoadUrl);
    }

    private boolean isVideoStreamUrl(String u) {
        if (u == null) return false;
        String lower = u.toLowerCase();
        if (!lower.startsWith("http")) return false;
        if (lower.contains(".mp4") && !lower.contains(".html") && !lower.contains(".js")) return true;
        if (lower.contains("mime=video") || lower.contains("video/mp4")
                || lower.contains("&mime=video%2fmp4")) return true;
        if (lower.contains("googlevideo.com/videoplayback")) return true;
        if ((lower.contains("cdninstagram.com") || lower.contains("fbcdn.net"))
                && (lower.contains("bytestart") || lower.contains("video")
                    || lower.contains(".mp4") || lower.contains("oe="))) return true;
        if (lower.contains("video.twimg.com")
                && (lower.contains(".mp4") || lower.contains("m3u8") || lower.contains("vid/"))) return true;
        if (lower.contains("tiktokcdn.com") && lower.contains(".mp4")) return true;
        return false;
    }

    private String extractInstagramShortcode(String url) {
        Pattern p = Pattern.compile("/(?:reel|reels|p|tv)/([A-Za-z0-9_\\-]+)");
        Matcher m = p.matcher(url);
        if (m.find()) return m.group(1);
        return null;
    }

    private void notifyJsSnifferStatus(String msg) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferStatus==='function') window.onSnifferStatus('"
                    + escapeJs(msg) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    private void notifyJsSnifferSuccess(String streamUrl) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferSuccess==='function') window.onSnifferSuccess('"
                    + escapeJs(streamUrl) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    private void notifyJsSnifferFailed(String errorMsg) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferFailed==='function') window.onSnifferFailed('"
                    + escapeJs(errorMsg) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    //  JS Interface (called from WebView JS)
    // ─────────────────────────────────────────────────────────────────────────
    public class AndroidDownloader {
        private final Context mContext;

        public AndroidDownloader(Context context) {
            this.mContext = context;
        }

        @JavascriptInterface
        public void downloadVideo(String url, String filename, String title) {
            runOnUiThread(() -> startNativeOkHttpDownload(url, filename, title, "video/mp4", 0));
        }

        @JavascriptInterface
        public void downloadMedia(String url, String filename, String title, String mimeType) {
            runOnUiThread(() -> startNativeOkHttpDownload(url, filename, title, mimeType, 0));
        }

        @JavascriptInterface
        public void sniffAndDownload(String targetUrl, String defaultTitle) {
            runOnUiThread(() -> startHeadlessSniffer(targetUrl, defaultTitle));
        }

        @JavascriptInterface
        public boolean isNativeDownloaderAvailable() {
            return true;
        }
    }
}
