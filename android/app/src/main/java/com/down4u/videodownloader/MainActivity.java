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
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;

public class MainActivity extends BridgeActivity {

    private static final String TAG = "Down4U_Downloader";
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService downloadExecutor = Executors.newSingleThreadExecutor();
    private OkHttpClient okHttpClient;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Initialize OkHttp client with optimized timeouts and redirect handling
        okHttpClient = new OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .followRedirects(true)
            .followSslRedirects(true)
            .retryOnConnectionFailure(true)
            .build();

        // Access WebView from Capacitor Bridge
        WebView webView = getBridge().getWebView();
        if (webView != null) {
            // Register AndroidDownloader JavascriptInterface
            webView.addJavascriptInterface(new AndroidDownloader(this), "AndroidDownloader");

            // Intercept any internal WebView download requests
            webView.setDownloadListener(new DownloadListener() {
                @Override
                public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                    startNativeOkHttpDownload(url, "video_" + System.currentTimeMillis() + ".mp4", "Video Download", mimetype);
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

    // ── High-Performance In-App OkHttp Downloader Engine ────────────────────────
    public void startNativeOkHttpDownload(String url, String filename, String title, String mimeType) {
        if (url == null || url.trim().isEmpty()) {
            Toast.makeText(this, "Download URL is invalid", Toast.LENGTH_SHORT).show();
            notifyJsDownloadComplete(false);
            return;
        }

        final String cleanTitle = (title != null && !title.trim().isEmpty()) ? title : "Video Download";
        String cleanName = (filename != null && !filename.trim().isEmpty()) ? filename : "video_" + System.currentTimeMillis() + ".mp4";
        cleanName = cleanName.replaceAll("[\\\\/:*?\"<>|]", "_");
        if (!cleanName.toLowerCase().endsWith(".mp4") && !cleanName.toLowerCase().endsWith(".jpg")) {
            cleanName += ".mp4";
        }
        final String targetFilename = cleanName;
        final String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : "video/mp4";

        Toast.makeText(this, "Download started...", Toast.LENGTH_SHORT).show();
        Log.i(TAG, "Starting OkHttp download for URL: " + url + " | File: " + targetFilename);

        downloadExecutor.execute(() -> {
            OutputStream outputStream = null;
            Uri mediaStoreUri = null;
            File targetFile = null;

            try {
                Request.Builder reqBuilder = new Request.Builder()
                    .url(url)
                    .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
                    .header("Accept", "*/*")
                    .header("Connection", "keep-alive");

                // Add platform-specific referers for CDN access
                if (url.contains("cdninstagram.com") || url.contains("fbcdn.net")) {
                    reqBuilder.header("Referer", "https://www.instagram.com/");
                } else if (url.contains("twimg.com")) {
                    reqBuilder.header("Referer", "https://twitter.com/");
                } else if (url.contains("tiktokcdn.com")) {
                    reqBuilder.header("Referer", "https://www.tiktok.com/");
                }

                Response response = okHttpClient.newCall(reqBuilder.build()).execute();
                if (!response.isSuccessful()) {
                    Log.e(TAG, "Server returned HTTP error: " + response.code() + " " + response.message());
                    mainHandler.post(() -> Toast.makeText(MainActivity.this, "Download failed (HTTP " + response.code() + ")", Toast.LENGTH_LONG).show());
                    notifyJsDownloadComplete(false);
                    return;
                }

                ResponseBody body = response.body();
                if (body == null) {
                    Log.e(TAG, "Empty response body");
                    notifyJsDownloadComplete(false);
                    return;
                }

                long totalBytes = body.contentLength();
                InputStream inputStream = body.byteStream();

                // Open destination OutputStream: MediaStore (Android 10+) or Direct File (Android 9-)
                ContentResolver resolver = getContentResolver();
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Video.Media.DISPLAY_NAME, targetFilename);
                    values.put(MediaStore.Video.Media.MIME_TYPE, safeMime);
                    values.put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Down4U");
                    values.put(MediaStore.Video.Media.IS_PENDING, 1);

                    mediaStoreUri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                    if (mediaStoreUri == null) {
                        mediaStoreUri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
                    }
                    if (mediaStoreUri != null) {
                        outputStream = resolver.openOutputStream(mediaStoreUri);
                    }
                }

                // Fallback for older Android or if MediaStore insert failed
                if (outputStream == null) {
                    File downloadsDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                    File appDir = new File(downloadsDir, "Down4U");
                    if (!appDir.exists()) appDir.mkdirs();
                    targetFile = new File(appDir, targetFilename);
                    outputStream = new FileOutputStream(targetFile);
                }

                // Stream bytes with progress reporting
                byte[] buffer = new byte[65536]; // 64 KB buffer
                int bytesRead;
                long totalDownloaded = 0;
                long lastProgressTime = 0;

                while ((bytesRead = inputStream.read(buffer)) != -1) {
                    outputStream.write(buffer, 0, bytesRead);
                    totalDownloaded += bytesRead;

                    long now = System.currentTimeMillis();
                    if (now - lastProgressTime > 200) { // Throttle callbacks to 5 times/sec
                        lastProgressTime = now;
                        int percent = (totalBytes > 0) ? (int) ((totalDownloaded * 100L) / totalBytes) : -1;
                        if (percent > 99) percent = 99; // 100% after complete scan
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

                // Scan into Gallery via MediaScannerConnection
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

                Log.i(TAG, "Download finished successfully! Total bytes: " + totalDownloaded);
                final long finalTotal = totalDownloaded;
                mainHandler.post(() -> {
                    Toast.makeText(MainActivity.this, "Download Complete! Saved to Gallery", Toast.LENGTH_SHORT).show();
                    notifyJsProgress(100, formatBytes(finalTotal), formatBytes(finalTotal));
                    notifyJsDownloadComplete(true);
                });

            } catch (Exception e) {
                Log.e(TAG, "Download failed with exception: " + e.getMessage(), e);
                // Clean up partial downloads on failure
                if (mediaStoreUri != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    try { getContentResolver().delete(mediaStoreUri, null, null); } catch (Exception ignored) {}
                }
                if (targetFile != null && targetFile.exists()) {
                    try { targetFile.delete(); } catch (Exception ignored) {}
                }
                mainHandler.post(() -> {
                    Toast.makeText(MainActivity.this, "Download error: " + e.getMessage(), Toast.LENGTH_LONG).show();
                    notifyJsDownloadComplete(false);
                });
            } finally {
                if (outputStream != null) {
                    try { outputStream.close(); } catch (Exception ignored) {}
                }
            }
        });
    }

    // ── JS Bridge: live progress callback ────────────────────────────────────
    private void notifyJsProgress(int percent, String downloaded, String total) {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) return;
        String safeDownloaded = escapeJs(downloaded);
        String safeTotal = escapeJs(total);
        String js = "if(typeof window.onNativeDownloadProgress==='function')" +
                    "window.onNativeDownloadProgress(" + percent + ",'" + safeDownloaded + "','" + safeTotal + "');";
        webView.evaluateJavascript(js, null);
    }

    // ── JS Bridge: gallery save complete → show true 100% ────────────────────
    private void notifyJsDownloadComplete(boolean success) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onNativeDownloadComplete==='function')" +
                        "window.onNativeDownloadComplete(" + success + ");";
            webView.evaluateJavascript(js, null);
        });
    }

    // ── Utilities ─────────────────────────────────────────────────────────────
    private String formatBytes(long bytes) {
        if (bytes < 0) return "0 B";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) return String.format("%.1f MB", bytes / (1024.0 * 1024));
        return String.format("%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    private String escapeJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "");
    }

    // ── Headless In-App Native Sniffer (Extracts videos directly on user device) ──
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
        settings.setUserAgentString("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36");

        final String cleanTitle = (defaultTitle != null && !defaultTitle.isEmpty()) ? defaultTitle : "Video";
        final String safeFilename = cleanTitle.replaceAll("[^a-zA-Z0-9_\\-]", "_") + "_" + System.currentTimeMillis() + ".mp4";

        notifyJsSnifferStatus("Connecting direct in-app stream extractor...");

        String loadUrl = targetUrl;
        if (targetUrl.contains("instagram.com/reel/") || targetUrl.contains("instagram.com/p/") || targetUrl.contains("instagram.com/tv/")) {
            String shortcode = extractInstagramShortcode(targetUrl);
            if (shortcode != null) {
                loadUrl = "https://www.instagram.com/reel/" + shortcode + "/embed/captioned/";
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

        headlessWebView.setWebViewClient(new android.webkit.WebViewClient() {
            @Override
            public android.webkit.WebResourceResponse shouldInterceptRequest(WebView view, android.webkit.WebResourceRequest request) {
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
                        startNativeOkHttpDownload(reqUrl, safeFilename, cleanTitle, "video/mp4");
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
                    "  if (v && v.src && v.src.indexOf('http') === 0) return v.src;" +
                    "  var s = document.querySelector('video source');" +
                    "  if (s && s.src && s.src.indexOf('http') === 0) return s.src;" +
                    "  return '';" +
                    "})()",
                    value -> {
                        if (value != null && value.length() > 6 && !value.equals("\"\"") && !value.equals("null") && isSniffing) {
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
                                    startNativeOkHttpDownload(foundUrl, safeFilename, cleanTitle, "video/mp4");
                                });
                            }
                        }
                    }
                );
            }
        });

        headlessWebView.loadUrl(loadUrl);
    }

    private boolean isVideoStreamUrl(String u) {
        if (u == null) return false;
        String lower = u.toLowerCase();
        if (!lower.startsWith("http")) return false;

        if (lower.contains(".mp4") && !lower.contains(".html") && !lower.contains(".js")) {
            return true;
        }

        if (lower.contains("mime=video") || lower.contains("video/mp4") || lower.contains("&mime=video%2fmp4")) {
            return true;
        }

        if (lower.contains("googlevideo.com/videoplayback")) {
            return true;
        }

        if ((lower.contains("cdninstagram.com") || lower.contains("fbcdn.net")) &&
            (lower.contains("bytestart") || lower.contains("video") || lower.contains(".mp4") || lower.contains("oe="))) {
            return true;
        }

        if (lower.contains("video.twimg.com") && (lower.contains(".mp4") || lower.contains("m3u8") || lower.contains("vid/"))) {
            return true;
        }

        if (lower.contains("tiktokcdn.com") && lower.contains(".mp4")) {
            return true;
        }

        return false;
    }

    private String extractInstagramShortcode(String url) {
        java.util.regex.Pattern p = java.util.regex.Pattern.compile("/(?:reel|reels|p|tv)/([A-Za-z0-9_\\-]+)");
        java.util.regex.Matcher m = p.matcher(url);
        if (m.find()) {
            return m.group(1);
        }
        return null;
    }

    private void notifyJsSnifferStatus(String msg) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferStatus==='function') window.onSnifferStatus('" + escapeJs(msg) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    private void notifyJsSnifferSuccess(String streamUrl) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferSuccess==='function') window.onSnifferSuccess('" + escapeJs(streamUrl) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    private void notifyJsSnifferFailed(String errorMsg) {
        mainHandler.post(() -> {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView == null) return;
            String js = "if(typeof window.onSnifferFailed==='function') window.onSnifferFailed('" + escapeJs(errorMsg) + "');";
            webView.evaluateJavascript(js, null);
        });
    }

    // ── Inner Class: JavaScript Interface ────────────────────────────────────
    public class AndroidDownloader {
        private final Context mContext;

        public AndroidDownloader(Context context) {
            this.mContext = context;
        }

        @JavascriptInterface
        public void downloadVideo(String url, String filename, String title) {
            runOnUiThread(() -> startNativeOkHttpDownload(url, filename, title, "video/mp4"));
        }

        @JavascriptInterface
        public void downloadMedia(String url, String filename, String title, String mimeType) {
            runOnUiThread(() -> startNativeOkHttpDownload(url, filename, title, mimeType));
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
