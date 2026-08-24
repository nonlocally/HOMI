package com.aadarwal.phonebridge;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.util.Map;

/**
 * homi — the window onto the fabric.
 *
 * NOT a rewrite of the board. The fabric already renders agents, merged
 * conversations and device state, and the mailbox is the source of truth for
 * all of it. Reimplementing any of that here would make the phone a SECOND
 * source of truth, which is the one thing this app must never become — a
 * phone is the device most likely to be lost, wiped, or flat, and history
 * that exists only here is unreachable exactly when it matters.
 *
 * So this is a shell that supplies the three things a browser on Android
 * cannot, all of which already work in this app:
 *
 *   speech in    Chrome's SpeechRecognition is a cloud round trip with a
 *                ~3-5s silence cutoff and no on-device path on Android.
 *                Ours is on-device and the audio never leaves the phone.
 *   speech out   speechSynthesis buffers the whole utterance before making
 *                a sound. A held TextToSpeech engine starts in ~11ms.
 *   notifications a web page cannot read or reply to them at all.
 *
 * The page calls those through window.homi (see the bridge below), so the
 * SAME board that works in a browser gets native capabilities here without
 * a separate mobile front end to keep in step.
 *
 * No layout resource, no icon asset, no strings file. The view is built in
 * code and the icon is a platform drawable, which is what keeps the whole
 * app to four build tools and no Gradle.
 */
public class Homi extends Activity {

    // The fabric's own front end. Reachable from the phone over Tailscale.
    private static final String BOARD = "https://agents.aadarwal.com/";

    private WebView web;
    private Voice voice;
    private Speak speak;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        voice = new Voice(this);
        speak = new Speak(this);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);      // the board keeps view state here
        s.setMediaPlaybackRequiresUserGesture(false);
        // Keep navigation INSIDE the app. Without this a link to anywhere
        // hands the person off to Chrome mid-conversation.
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient());
        web.addJavascriptInterface(new Bridge_(), "homi");

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        web.loadUrl(BOARD);
        // The bridge and the voice service belong to the app, not to this
        // window — opening homi should not be what makes voice work.
        VoiceService.ensureRunning(this);
    }

    @Override
    public void onBackPressed() {
        // Back walks the conversation history, not out of the app. Leaving
        // a chat by exiting to the launcher is the wrong shape.
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    /** Hand a result back to the page, on the UI thread as WebView requires. */
    private void toPage(final String js) {
        runOnUiThread(() -> {
            if (web != null) web.evaluateJavascript(js, null);
        });
    }

    private static String jsStr(String s) {
        if (s == null) return "''";
        StringBuilder b = new StringBuilder("'");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\'': b.append("\\'"); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': break;
                // U+2028/9 terminate a JS line even inside a string.
                case '\u2028': b.append("\\u2028"); break;
                case '\u2029': b.append("\\u2029"); break;
                default: b.append(c);
            }
        }
        return b.append("'").toString();
    }

    /**
     * What the page can call. Deliberately small: speech in, speech out, and
     * whether either is available. Everything else the board already does
     * over HTTP against the fabric and should keep doing — a capability
     * added here is one the browser version silently loses.
     *
     * EVERY METHOD RETURNS IMMEDIATELY and answers through a callback.
     * A @JavascriptInterface method runs on WebView's own Java thread, but
     * the JS caller BLOCKS on the return value — so a `say` that waited for
     * speech to finish would freeze the page for the length of the sentence,
     * and a `listen` would freeze it for as long as nobody spoke. The first
     * version of this file did exactly that.
     */
    private final class Bridge_ {

        /** True when this is the app rather than a browser, so the page can
         *  take the native path instead of Web Speech. */
        @JavascriptInterface
        public boolean available() {
            return true;
        }

        /** Starts listening. Calls window.__homiHeard(text) with the
         *  transcript, or with '' if nothing was heard. */
        @JavascriptInterface
        public void listen(final int seconds) {
            new Thread(() -> {
                String text = "";
                try {
                    Map<String, Object> got =
                        voice.listen(seconds > 0 ? seconds : 20);
                    Object t = got.get("text");
                    if (Boolean.TRUE.equals(got.get("ok")) && t != null) {
                        text = t.toString();
                    }
                } catch (Throwable e) {
                    Log.e(Listener.TAG, "homi.listen failed", e);
                }
                toPage("window.__homiHeard && window.__homiHeard("
                       + jsStr(text) + ")");
            }, "homi-listen").start();
        }

        /** Starts speaking. Calls window.__homiSpoke(ok) when the utterance
         *  has actually finished — so a page that speaks twice in a row can
         *  wait rather than talking over itself. */
        @JavascriptInterface
        public void say(final String text) {
            new Thread(() -> {
                boolean ok = false;
                try {
                    ok = Boolean.TRUE.equals(speak.say(text, 90).get("ok"));
                } catch (Throwable e) {
                    Log.e(Listener.TAG, "homi.say failed", e);
                }
                toPage("window.__homiSpoke && window.__homiSpoke("
                       + (ok ? "true" : "false") + ")");
            }, "homi-say").start();
        }

        @JavascriptInterface
        public void stopSpeaking() {
            speak.stop();
        }
    }
}
