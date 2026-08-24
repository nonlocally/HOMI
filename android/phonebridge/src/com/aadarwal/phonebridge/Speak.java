package com.aadarwal.phonebridge;

import android.content.Context;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Text to speech, held WARM.
 *
 * This is the actual fix for "the voice back is kind of slow", and the
 * slowness was never the engine. The two paths in use before both paid a
 * startup cost per sentence:
 *
 *   termux-tts-speak   spawns a process, which binds a TextToSpeech engine,
 *                      waits for onInit, speaks, and tears the whole thing
 *                      down again — every single time.
 *   speechSynthesis    in Chrome buffers the entire utterance before making
 *                      any sound at all.
 *
 * One engine, initialised once and kept, removes that. The same lever as
 * every other latency fix tonight: residency, not a faster component.
 *
 * It also REPORTS when speech actually finished, rather than returning the
 * moment the request was queued. `speak` that returns before a word is
 * audible is the same lie as `input tap` exiting 0 — and it matters here
 * because an agent that speaks twice in a row would otherwise talk over
 * itself.
 */
class Speak {

    private final Context ctx;
    private TextToSpeech tts;
    private final AtomicBoolean ready = new AtomicBoolean(false);
    private final CountDownLatch initDone = new CountDownLatch(1);

    Speak(Context ctx) {
        this.ctx = ctx;
        init();
    }

    private void init() {
        tts = new TextToSpeech(ctx, status -> {
            ready.set(status == TextToSpeech.SUCCESS);
            if (ready.get()) {
                tts.setLanguage(Locale.US);
                Log.i(Listener.TAG, "tts ready");
            } else {
                Log.e(Listener.TAG, "tts init failed: " + status);
            }
            initDone.countDown();
        });
    }

    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        await();
        out.put("ready", ready.get());
        if (ready.get()) {
            try {
                out.put("engine", tts.getDefaultEngine());
                out.put("voice", String.valueOf(tts.getVoice()));
            } catch (Throwable t) {
                out.put("detail", String.valueOf(t.getMessage()));
            }
        }
        return out;
    }

    private void await() {
        try {
            initDone.await(15, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * Say it, and wait until it has actually been said.
     *
     * Returns the time to FIRST AUDIO separately from total duration,
     * because those answer different questions: first-audio is what a person
     * experiences as responsiveness, total is how long before you may speak
     * again without collision.
     */
    Map<String, Object> say(String text, int timeoutSeconds) {
        Map<String, Object> out = new LinkedHashMap<>();
        await();
        if (!ready.get()) {
            out.put("ok", false);
            out.put("err", "no text-to-speech engine is available");
            return out;
        }
        if (text == null || text.trim().isEmpty()) {
            out.put("ok", false);
            out.put("err", "refusing to speak an empty string");
            return out;
        }
        final String id = "u" + System.nanoTime();
        final long t0 = System.currentTimeMillis();
        final AtomicLong firstAudio = new AtomicLong(0);
        final AtomicReference<String> err = new AtomicReference<>(null);
        final CountDownLatch done = new CountDownLatch(1);

        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override public void onStart(String utteranceId) {
                firstAudio.compareAndSet(0, System.currentTimeMillis() - t0);
            }
            @Override public void onDone(String utteranceId) {
                done.countDown();
            }
            @Override public void onError(String utteranceId) {
                err.set("the engine reported an error");
                done.countDown();
            }
            @Override public void onError(String utteranceId, int code) {
                err.set("engine error " + code);
                done.countDown();
            }
        });

        Bundle params = new Bundle();
        int q = tts.speak(text, TextToSpeech.QUEUE_FLUSH, params, id);
        if (q != TextToSpeech.SUCCESS) {
            out.put("ok", false);
            out.put("err", "the engine refused the utterance");
            return out;
        }
        boolean finished;
        try {
            finished = done.await(timeoutSeconds, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            finished = false;
        }
        long total = System.currentTimeMillis() - t0;
        if (err.get() != null) {
            out.put("ok", false);
            out.put("err", err.get());
            return out;
        }
        if (!finished) {
            // Speech may well still be playing. Say that, rather than
            // reporting a failure that did not happen.
            out.put("ok", false);
            out.put("err", "still speaking after " + timeoutSeconds
                           + "s — not confirmed finished");
            out.put("first_audio_ms", firstAudio.get());
            return out;
        }
        out.put("ok", true);
        out.put("first_audio_ms", firstAudio.get());
        out.put("total_ms", total);
        out.put("chars", text.length());
        return out;
    }

    void stop() {
        if (tts != null) {
            try { tts.stop(); } catch (Throwable ignored) {}
        }
    }
}
