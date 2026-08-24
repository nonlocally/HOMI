package com.aadarwal.phonebridge;

import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognitionSupport;
import android.speech.RecognitionSupportCallback;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Speech to text, ON THE DEVICE.
 *
 * The web page could never do this. Chrome's SpeechRecognition on Android is
 * a round trip to Google with a ~3-5s silence cutoff, a documented-broken
 * continuous mode, and no on-device path at all — Chrome's on-device speech
 * API explicitly excludes Android, so there is no version to wait for. Those
 * are CHROME limits. Android has had createOnDeviceSpeechRecognizer since
 * API 31, and this phone ships two RecognitionServices.
 *
 * So the audio never leaves the phone, there is no model on the mini, no
 * network leg, and nothing to keep warm on another machine.
 *
 * THREADING is the whole difficulty. SpeechRecognizer's own doc says its
 * methods "must be invoked only from the main application thread", and AOSP
 * enforces it — checkIsCalledFromMainThread() throws outright. The bridge
 * answers on its own socket thread, so every call here hops to the main
 * Looper and the caller waits on a latch.
 */
class Voice {

    private final Context ctx;
    private final Handler main = new Handler(Looper.getMainLooper());

    Voice(Context ctx) { this.ctx = ctx; }

    /** Availability, and WHICH languages are ready versus merely supported. */
    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        boolean avail = SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx);
        out.put("on_device_available", avail);
        if (!avail) {
            // isOnDeviceRecognitionAvailable resolves a resource overlay
            // (config_defaultOnDeviceSpeechRecognitionService), NOT the
            // voice_recognition_service secure setting — so a device can have
            // RecognitionServices installed and still answer false here.
            out.put("detail", "no on-device recognition service is configured "
                            + "in this build's overlay");
            return out;
        }
        final AtomicReference<Map<String, Object>> box = new AtomicReference<>();
        final CountDownLatch done = new CountDownLatch(1);
        main.post(() -> {
            SpeechRecognizer r = null;
            try {
                r = SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx);
                final SpeechRecognizer rr = r;
                rr.checkRecognitionSupport(intent(false), ctx.getMainExecutor(),
                    new RecognitionSupportCallback() {
                        @Override
                        public void onSupportResult(RecognitionSupport s) {
                            Map<String, Object> m = new LinkedHashMap<>();
                            m.put("installed", s.getInstalledOnDeviceLanguages());
                            m.put("supported", s.getSupportedOnDeviceLanguages());
                            m.put("pending", s.getPendingOnDeviceLanguages());
                            box.set(m);
                            rr.destroy();
                            done.countDown();
                        }
                        @Override
                        public void onError(int error) {
                            Map<String, Object> m = new LinkedHashMap<>();
                            m.put("support_error", describe(error));
                            box.set(m);
                            rr.destroy();
                            done.countDown();
                        }
                    });
            } catch (Throwable t) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("create_error", String.valueOf(t.getMessage()));
                box.set(m);
                if (r != null) try { r.destroy(); } catch (Throwable ignored) {}
                done.countDown();
            }
        });
        try {
            if (!done.await(20, TimeUnit.SECONDS)) {
                out.put("detail", "checkRecognitionSupport never called back");
                return out;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        Map<String, Object> got = box.get();
        if (got != null) out.putAll(got);
        return out;
    }

    /** Ask the recognition service to fetch a language model it supports but
     *  has not downloaded. onScheduled means it may be deferred indefinitely,
     *  so this reports what happened rather than pretending to be done. */
    Map<String, Object> download(int waitSeconds) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)) {
            out.put("ok", false);
            out.put("detail", "no on-device recogniser to download for");
            return out;
        }
        final AtomicReference<String> state = new AtomicReference<>("timeout");
        final CountDownLatch done = new CountDownLatch(1);
        main.post(() -> {
            try {
                SpeechRecognizer r = SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx);
                r.triggerModelDownload(intent(false), ctx.getMainExecutor(),
                    new android.speech.ModelDownloadListener() {
                        @Override public void onProgress(int pct) {
                            state.set("downloading " + pct + "%");
                        }
                        @Override public void onSuccess() {
                            state.set("ready"); done.countDown();
                        }
                        @Override public void onScheduled() {
                            // No further callbacks will fire on this listener.
                            state.set("scheduled — the service deferred it, "
                                    + "possibly until wifi or charging");
                            done.countDown();
                        }
                        @Override public void onError(int e) {
                            state.set("error: " + describe(e)); done.countDown();
                        }
                    });
            } catch (Throwable t) {
                state.set("error: " + t.getMessage());
                done.countDown();
            }
        });
        try { done.await(waitSeconds, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        out.put("ok", true);
        out.put("state", state.get());
        return out;
    }

    /**
     * Listen once and return what was heard.
     *
     * Blocking by design: one utterance in, one transcript out, the same
     * shape as every other op on this bridge. A caller that wants continuous
     * listening calls it in a loop and can stop whenever it likes — which is
     * also the only pattern Android actually supports, since there is no
     * documented long-running mode beyond segmented sessions.
     */
    Map<String, Object> listen(int timeoutSeconds) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)) {
            out.put("ok", false);
            out.put("err", "on-device recognition is not available here");
            return out;
        }
        final AtomicReference<String> text = new AtomicReference<>(null);
        final AtomicReference<String> partial = new AtomicReference<>(null);
        final AtomicReference<String> error = new AtomicReference<>(null);
        final CountDownLatch done = new CountDownLatch(1);
        final AtomicReference<SpeechRecognizer> ref = new AtomicReference<>();
        // Which callbacks fired, in order. A timeout with an EMPTY trace
        // means the recogniser never started — a different failure entirely
        // from one that started and heard nothing, and indistinguishable
        // from the outside without this.
        final List<String> trace = java.util.Collections.synchronizedList(
            new ArrayList<String>());

        main.post(() -> {
            try {
                SpeechRecognizer r = SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx);
                ref.set(r);
                r.setRecognitionListener(new RecognitionListener() {
                    @Override public void onReadyForSpeech(Bundle p) {
                        trace.add("ready");
                    }
                    @Override public void onBeginningOfSpeech() {
                        trace.add("speech-began");
                    }
                    @Override public void onRmsChanged(float v) {
                        if (trace.isEmpty() || !trace.contains("rms")) {
                            trace.add("rms");   // the mic is delivering audio
                        }
                    }
                    @Override public void onBufferReceived(byte[] b) {}
                    @Override public void onEndOfSpeech() {
                        trace.add("speech-ended");
                    }
                    @Override public void onEvent(int t, Bundle p) {}

                    @Override public void onPartialResults(Bundle b) {
                        String s = first(b);
                        if (s != null) partial.set(s);
                    }

                    @Override public void onResults(Bundle b) {
                        text.set(first(b));
                        done.countDown();
                    }

                    @Override public void onError(int e) {
                        trace.add("error:" + describe(e));
                        // A partial that arrived before the error is still
                        // real speech. Losing it because the recogniser then
                        // timed out on silence would be throwing away the
                        // answer we were given.
                        if (partial.get() != null) {
                            text.set(partial.get());
                        } else {
                            error.set(describe(e));
                        }
                        done.countDown();
                    }
                });
                r.startListening(intent(true));
            } catch (Throwable t) {
                error.set(String.valueOf(t.getMessage()));
                done.countDown();
            }
        });

        boolean finished = false;
        try {
            finished = done.await(timeoutSeconds, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // destroy() is main-thread-only as well, and leaking recognisers
        // eventually wedges the recognition service for every app.
        main.post(() -> {
            SpeechRecognizer r = ref.get();
            if (r != null) try { r.destroy(); } catch (Throwable ignored) {}
        });

        if (!finished) {
            out.put("ok", false);
            out.put("err", trace.isEmpty()
                    ? "the recogniser never started — no callbacks at all in "
                      + timeoutSeconds + "s (a background app may not hold "
                      + "the mic; needs a foreground service)"
                    : "nothing heard within " + timeoutSeconds + "s");
            out.put("trace", new ArrayList<>(trace));
            return out;
        }
        if (text.get() != null) {
            out.put("ok", true);
            out.put("text", text.get());
            out.put("trace", new ArrayList<>(trace));
            out.put("partial_only", text.get().equals(partial.get())
                                    && error.get() != null);
            return out;
        }
        out.put("ok", false);
        out.put("err", error.get() == null ? "no result" : error.get());
        out.put("trace", new ArrayList<>(trace));
        return out;
    }

    private static String first(Bundle b) {
        if (b == null) return null;
        ArrayList<String> l = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return (l == null || l.isEmpty()) ? null : l.get(0);
    }

    private Intent intent(boolean partials) {
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                   RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US");
        if (partials) {
            i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        }
        return i;
    }

    /** The numeric error codes are useless in a log. Say what happened. */
    static String describe(int e) {
        switch (e) {
            case SpeechRecognizer.ERROR_AUDIO: return "audio recording failed";
            case SpeechRecognizer.ERROR_CLIENT: return "client side error";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "RECORD_AUDIO is not granted";
            case SpeechRecognizer.ERROR_NETWORK: return "network error";
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "network timeout";
            case SpeechRecognizer.ERROR_NO_MATCH: return "nothing recognised";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "the recogniser is busy";
            case SpeechRecognizer.ERROR_SERVER: return "server error";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT: return "no speech heard";
            case SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED:
                return "language not supported";
            case SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE:
                return "language supported but not downloaded";
            case SpeechRecognizer.ERROR_CANNOT_CHECK_SUPPORT:
                return "cannot check support";
            default: return "error " + e;
        }
    }
}
