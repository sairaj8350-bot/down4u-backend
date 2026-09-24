package com.down4u.videodownloader;

import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.Toast;

import com.getcapacitor.BridgeActivity;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public class MainActivity extends BridgeActivity {

    private BroadcastReceiver onDownloadCompleteReceiver;

    // Real-time progress polling
    private ScheduledExecutorService progressScheduler;
    private ScheduledFuture<?> progressFuture;
    private long activeDownloadId = -1;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        progressScheduler = Executors.newSingleThreadScheduledExecutor();

        // Access WebView from Capacitor Bridge
        WebView webView = getBridge().getWebView();
        if (webView != null) {
            // Register AndroidDownloader JavascriptInterface
            webView.addJavascriptInterface(new AndroidDownloader(this), "AndroidDownloader");

            // Intercept any WebView download requests to prevent external browser from opening
            webView.setDownloadListener(new DownloadListener() {
                @Override
                public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                    startNativeDownload(url, "video_" + System.currentTimeMillis() + ".mp4", "Video Download", mimetype);
                }
            });
        }

        // BroadcastReceiver: DownloadManager complete → MediaScanner → notify JS with true 100%
        onDownloadCompleteReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
                if (id == -1 || id != activeDownloadId) return;

                stopProgressPolling();

                DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                DownloadManager.Query query = new DownloadManager.Query();
                query.setFilterById(id);

                try (Cursor cursor = dm.query(query)) {
                    if (cursor != null && cursor.moveToFirst()) {
                        int statusIndex = cursor.getColumnIndex(DownloadManager.COLUMN_STATUS);
                        int status = (statusIndex != -1) ? cursor.getInt(statusIndex) : -1;

                        if (status == DownloadManager.STATUS_SUCCESSFUL) {
                            int uriIndex = cursor.getColumnIndex(DownloadManager.COLUMN_LOCAL_URI);
                            String localUriString = (uriIndex != -1) ? cursor.getString(uriIndex) : null;

                            if (localUriString != null) {
                                Uri fileUri = Uri.parse(localUriString);
                                String filePath = fileUri.getPath();
                                if (filePath != null) {
                                    // Scan file into Gallery, THEN notify JS of 100%
                                    MediaScannerConnection.scanFile(
                                        context,
                                        new String[]{filePath},
                                        new String[]{"video/mp4", "image/jpeg"},
                                        (path, uri) -> {
                                            Intent mediaScanIntent = new Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE);
                                            mediaScanIntent.setData(uri);
                                            context.sendBroadcast(mediaScanIntent);
                                            // Only NOW show 100% — file is truly in Gallery
                                            notifyJsDownloadComplete(true);
                                        }
                                    );
                                } else {
                                    notifyJsDownloadComplete(true);
                                }
                            } else {
                                notifyJsDownloadComplete(true);
                            }
                        } else {
                            // Download failed
                            notifyJsDownloadComplete(false);
                        }
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                    notifyJsDownloadComplete(false);
                }

                activeDownloadId = -1;
            }
        };

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(onDownloadCompleteReceiver,
                new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
                Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(onDownloadCompleteReceiver,
                new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE));
        }
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        stopProgressPolling();
        if (progressScheduler != null && !progressScheduler.isShutdown()) {
            progressScheduler.shutdownNow();
        }
        if (onDownloadCompleteReceiver != null) {
            try {
                unregisterReceiver(onDownloadCompleteReceiver);
            } catch (Exception ignored) {}
        }
    }

    // ── Start Native Download & begin real-time progress polling ─────────────
    public void startNativeDownload(String url, String filename, String title, String mimeType) {
        try {
            if (url == null || url.trim().isEmpty()) {
                Toast.makeText(this, "Download URL is invalid", Toast.LENGTH_SHORT).show();
                return;
            }

            String cleanTitle = (title != null && !title.trim().isEmpty()) ? title : "Video Download";
            String cleanName = (filename != null && !filename.trim().isEmpty()) ? filename : "video_" + System.currentTimeMillis() + ".mp4";
            cleanName = cleanName.replaceAll("[\\\\/:*?\"<>|]", "_");
            if (!cleanName.toLowerCase().endsWith(".mp4") && !cleanName.toLowerCase().endsWith(".jpg")) {
                cleanName += ".mp4";
            }

            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(cleanTitle);
            request.setDescription("Downloading to Gallery...");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, cleanName);

            String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : "video/mp4";
            request.setMimeType(safeMime);
            request.addRequestHeader("User-Agent", "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36");

            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (dm != null) {
                stopProgressPolling(); // cancel any previous polling
                long downloadId = dm.enqueue(request);
                activeDownloadId = downloadId;
                Toast.makeText(this, "Download started!", Toast.LENGTH_SHORT).show();
                startProgressPolling(dm, downloadId);
            } else {
                Toast.makeText(this, "System DownloadManager not available", Toast.LENGTH_SHORT).show();
            }
        } catch (Exception e) {
            e.printStackTrace();
            Toast.makeText(this, "Download error: " + e.getMessage(), Toast.LENGTH_SHORT).show();
        }
    }

    // ── Poll DownloadManager every 500ms, push % to JS ───────────────────────
    private void startProgressPolling(DownloadManager dm, long downloadId) {
        progressFuture = progressScheduler.scheduleWithFixedDelay(() -> {
            try {
                DownloadManager.Query query = new DownloadManager.Query();
                query.setFilterById(downloadId);
                try (Cursor cursor = dm.query(query)) {
                    if (cursor == null || !cursor.moveToFirst()) return;

                    int statusIdx = cursor.getColumnIndex(DownloadManager.COLUMN_STATUS);
                    int status = (statusIdx != -1) ? cursor.getInt(statusIdx) : -1;

                    // Terminal states handled by BroadcastReceiver
                    if (status == DownloadManager.STATUS_SUCCESSFUL ||
                        status == DownloadManager.STATUS_FAILED) return;

                    int downloadedIdx = cursor.getColumnIndex(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR);
                    int totalIdx = cursor.getColumnIndex(DownloadManager.COLUMN_TOTAL_SIZE_BYTES);

                    long downloaded = (downloadedIdx != -1) ? cursor.getLong(downloadedIdx) : 0;
                    long total = (totalIdx != -1) ? cursor.getLong(totalIdx) : -1;

                    int percent;
                    String downloadedStr = formatBytes(downloaded);
                    String totalStr;

                    if (total > 0 && downloaded >= 0) {
                        percent = (int) ((downloaded * 100L) / total);
                        // Cap at 99% — true 100% only fires after MediaScanner gallery save
                        if (percent > 99) percent = 99;
                        totalStr = formatBytes(total);
                    } else {
                        // Unknown total size — indeterminate
                        percent = -1;
                        totalStr = "?";
                    }

                    final int finalPercent = percent;
                    final String finalDownloaded = downloadedStr;
                    final String finalTotal = totalStr;

                    mainHandler.post(() -> notifyJsProgress(finalPercent, finalDownloaded, finalTotal));
                }
            } catch (Exception ignored) {}
        }, 300, 500, TimeUnit.MILLISECONDS);
    }

    private void stopProgressPolling() {
        if (progressFuture != null && !progressFuture.isDone()) {
            progressFuture.cancel(false);
            progressFuture = null;
        }
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
                        startNativeDownload(reqUrl, safeFilename, cleanTitle, "video/mp4");
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
                                    startNativeDownload(foundUrl, safeFilename, cleanTitle, "video/mp4");
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

        if ((lower.contains("cdninstagram.com") || lower.contains("fbcdn.net")) &&
            (lower.contains("bytestart") || lower.contains("video") || lower.contains(".mp4") || lower.contains("oe="))) {
            return true;
        }

        if (lower.contains("video.twimg.com") && lower.contains(".mp4")) {
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
            runOnUiThread(() -> startNativeDownload(url, filename, title, "video/mp4"));
        }

        @JavascriptInterface
        public void downloadMedia(String url, String filename, String title, String mimeType) {
            runOnUiThread(() -> startNativeDownload(url, filename, title, mimeType));
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
