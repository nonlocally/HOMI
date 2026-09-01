package com.aadarwal.phonebridge;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Meta's Muse Voice Transcribe as the ears, for everything on-device cannot
 * hear.
 *
 * It replaced Saaras on 2026-09-01. Same job — Hindi and code-mixed speech,
 * which this phone has no local recogniser for — done better on every axis
 * that could be measured from the outside: #1 on the Artificial Analysis
 * streaming STT leaderboard the day it launched, 3.1% WER, code-switching
 * inside a sentence as a native capability rather than a mode flag, and
 * $0.18 an hour. Two providers for one job would have been surface without
 * a purpose, so Saaras's listen path is gone rather than demoted.
 *
 * TWO MODES, one class, because they are the same service at two latencies:
 *
 *   http    record until silence, POST the WAV, wait. The endpointing is
 *           ours — an RMS threshold in {@link Audio#record} — because a file
 *           API cannot know when someone stopped. Proven shape, drop-in for
 *           what Saaras did.
 *   stream  a WebSocket: audio goes up as it is spoken, partial transcripts
 *           come back while the person is still talking, and Muse's OWN
 *           endpointer (mode ENDPOINTING) says when the turn is over. No
 *           post-speech wait, no invented silence heuristic. This is the
 *           reason to have chosen Muse at all.
 *
 * Stream falls back to http if the connection cannot be established — and
 * ONLY then. Once the microphone is open the person is speaking; a failure
 * after that point is reported, never retried, because a silent retry makes
 * them say it twice.
 */
class Muse {

    static final String HOST = "api.meta.ai";
    static final String HTTP_URL = "https://api.meta.ai/v1/asr/transcribe";
    static final String WS_PATH = "/v1/asr/realtime";
    static final String MODEL = "muse-voice-transcribe-1.0";

    /** Same place, same reasoning, as every other key on this device. */
    private static final String KEY_FILE = "muse_key";

    /** How long to wait for the person to START before giving up. */
    private static final int NO_SPEECH_MS = 8_000;

    /** After the server says speechEnd, how long to wait for speechComplete. */
    private static final int COMPLETE_GRACE_MS = 2_500;

    private final Context ctx;
    private final Audio audio;
    private volatile String key;

    Muse(Context ctx, Audio audio) {
        this.ctx = ctx;
        this.audio = audio;
    }

    // ------------------------------------------------------------- the key

    String key() {
        if (key != null) return key;
        synchronized (this) {
            if (key != null) return key;
            try {
                File dir = ctx.getExternalFilesDir(null);
                if (dir == null) return null;
                File f = new File(dir, KEY_FILE);
                if (!f.exists()) return null;
                byte[] b = new byte[512];
                int n;
                try (FileInputStream in = new FileInputStream(f)) {
                    n = in.read(b);
                }
                if (n <= 0) return null;
                String s = new String(b, 0, n, StandardCharsets.UTF_8).trim();
                key = s.isEmpty() ? null : s;
            } catch (Exception e) {
                Log.w(Listener.TAG, "could not read the muse key", e);
            }
            return key;
        }
    }

    boolean available() {
        return key() != null;
    }

    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("provider", "muse");
        out.put("configured", available());
        out.put("model", MODEL);
        out.put("tts", false);
        if (!available()) out.put("detail", "no key at <externalFilesDir>/" + KEY_FILE);
        return out;
    }

    // ------------------------------------------------------------- listen

    /**
     * @param languageBias Muse takes language NAMES ("Hindi", "English"),
     *                     not codes. Empty means "detect".
     * @param stream       try the WebSocket first; fall back to http if the
     *                     connection cannot be made.
     */
    Map<String, Object> listen(int timeoutSeconds, List<String> languageBias,
                               boolean stream, Voices.Progress p) {
        Map<String, Object> out = new LinkedHashMap<>();
        String k = key();
        if (k == null) {
            out.put("ok", false);
            out.put("err", "muse is not configured (no key on this device)");
            return out;
        }
        if (stream) {
            try {
                return listenStream(k, timeoutSeconds, languageBias, p);
            } catch (NotConnected e) {
                Log.w(Listener.TAG, "muse stream unavailable, using http: " + e.getMessage());
                if (p != null) p.at("LISTENING", "muse (stream failed, one-shot)");
                Map<String, Object> r = listenHttp(k, timeoutSeconds, languageBias);
                r.put("fell_back_from", "muse-stream");
                r.put("fell_back_because", e.getMessage());
                return r;
            }
        }
        if (p != null) p.at("LISTENING", "muse");
        return listenHttp(k, timeoutSeconds, languageBias);
    }

    // ---------------------------------------------------------------- http

    private Map<String, Object> listenHttp(String k, int timeoutSeconds, List<String> bias) {
        Map<String, Object> out = new LinkedHashMap<>();
        long t0 = System.currentTimeMillis();
        Audio.Recorded rec;
        try {
            rec = Audio.record(Math.min(timeoutSeconds * 1000, Audio.MAX_RECORD_MS));
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "the microphone failed: " + String.valueOf(e.getMessage()));
            return out;
        }
        if (!rec.heardSpeech) {
            out.put("ok", false);
            out.put("err", "didn't catch that");
            out.put("recorded_ms", rec.ms);
            return out;
        }
        long recordedAt = System.currentTimeMillis();
        try {
            byte[] wav = Audio.wrapWav(rec.pcm, Audio.RECORD_RATE);
            JSONObject req = new JSONObject();
            // One clip is one turn. PUSH_TO_TALK tells Muse not to look for
            // turn boundaries inside it — we already decided where the turn
            // ended when we stopped recording.
            req.put("mode", "PUSH_TO_TALK");
            req.put("model", MODEL);
            req.put("audioEncoding", "WAV");
            if (bias != null && !bias.isEmpty()) req.put("languageBias", new JSONArray(bias));
            String resp = postMultipart(HTTP_URL + "?sessionId=" + UUID.randomUUID(),
                                        k, req.toString(), wav, timeoutSeconds);
            JSONObject o = new JSONObject(resp);
            String transcript = o.optString("transcript", "").trim();
            out.put("ok", !transcript.isEmpty());
            out.put("text", transcript);
            out.put("recorded_ms", rec.ms);
            out.put("api_ms", System.currentTimeMillis() - recordedAt);
            out.put("total_ms", System.currentTimeMillis() - t0);
            out.put("audio_ms", o.optLong("audioDurationMs", -1));
            out.put("provider", "muse");
            out.put("mode", "http");
            if (transcript.isEmpty()) out.put("err", "didn't catch that");
        } catch (ApiFailure e) {
            out.put("ok", false);
            out.put("err", e.getMessage());
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "muse failed: " + String.valueOf(e.getMessage()));
        }
        return out;
    }

    // -------------------------------------------------------------- stream

    /** Thrown only BEFORE the microphone opens; the caller may fall back. */
    static final class NotConnected extends Exception {
        NotConnected(String m) { super(m); }
    }

    private Map<String, Object> listenStream(String k, int timeoutSeconds,
                                             List<String> bias, Voices.Progress p)
            throws NotConnected {
        Map<String, Object> out = new LinkedHashMap<>();
        long t0 = System.currentTimeMillis();
        Ws ws;
        try {
            ws = Ws.connect(HOST, WS_PATH + "?sessionId=" + UUID.randomUUID(),
                            10_000, (timeoutSeconds + 15) * 1000);
        } catch (IOException e) {
            throw new NotConnected("connect: " + e.getMessage());
        }

        final AtomicReference<String> partial = new AtomicReference<>("");
        final AtomicReference<String> complete = new AtomicReference<>(null);
        final AtomicReference<String> error = new AtomicReference<>(null);
        final AtomicBoolean speechStarted = new AtomicBoolean(false);
        final AtomicLong speechEndedAt = new AtomicLong(0);
        final AtomicLong firstPartialAt = new AtomicLong(0);
        final CountDownLatch acked = new CountDownLatch(1);
        final CountDownLatch done = new CountDownLatch(1);

        // The reader owns recv(); the recording loop below owns send(). The
        // socket's two streams are independent, so this needs no lock.
        Thread reader = new Thread(() -> {
            try {
                while (true) {
                    Ws.Frame f = ws.recv();
                    if (f == null || f.opcode == Ws.OP_CLOSE) break;
                    if (f.opcode != Ws.OP_TEXT) continue;
                    JSONObject m = new JSONObject(f.text());
                    if (m.has("sessionId") && !m.has("type")) {
                        acked.countDown();                        // handshake ok
                        continue;
                    }
                    String type = m.optString("type", "");
                    switch (type) {
                        case "transcript": {
                            String t = m.optString("transcript", "");
                            if (!t.isEmpty()) {
                                partial.set(t);
                                firstPartialAt.compareAndSet(0, System.currentTimeMillis());
                                if (p != null) p.at("LISTENING", t);
                            }
                            break;
                        }
                        case "speechStart":
                            speechStarted.set(true);
                            break;
                        case "speechEnd":
                            speechEndedAt.compareAndSet(0, System.currentTimeMillis());
                            break;
                        case "speechComplete":
                            complete.set(m.optString("transcript", partial.get()));
                            done.countDown();
                            break;
                        case "error":
                            error.set(m.optString("message", "muse error"));
                            acked.countDown();                    // unblock a waiting handshake
                            done.countDown();
                            break;
                        default:
                            break;
                    }
                }
            } catch (Throwable t) {
                if (!ws.isClosed()) error.compareAndSet(null, "stream: " + t.getMessage());
            } finally {
                acked.countDown();
                done.countDown();
            }
        }, "muse-reader");
        reader.setDaemon(true);

        try {
            JSONObject hs = new JSONObject();
            hs.put("authorization", new JSONObject().put("accessToken", "Bearer " + k));
            hs.put("audioEncoding", "PCM_16KHZ");
            hs.put("model", MODEL);
            // ENDPOINTING: the server decides when the turn is over. This is
            // the whole point — a trained endpointer instead of our RMS guess.
            hs.put("mode", "ENDPOINTING");
            hs.put("partialMode", "CUMULATIVE");
            hs.put("emitAudioProgress", false);
            if (bias != null && !bias.isEmpty()) hs.put("languageBias", new JSONArray(bias));
            ws.sendText(hs.toString());
            reader.start();
            if (!acked.await(10, TimeUnit.SECONDS)) {
                ws.closeQuietly();
                throw new NotConnected("no handshake ack within 10s");
            }
            if (error.get() != null) {
                ws.closeQuietly();
                throw new NotConnected("handshake: " + error.get());
            }
        } catch (NotConnected e) {
            throw e;
        } catch (Exception e) {
            ws.closeQuietly();
            throw new NotConnected("handshake: " + e.getMessage());
        }

        // From here on the microphone is open and the person is speaking.
        // Failures are RESULTS now, not reasons to fall back.
        if (p != null) p.at("LISTENING", "muse · streaming");
        android.media.AudioRecord mic;
        try {
            mic = Audio.openMic();
        } catch (Exception e) {
            ws.close();
            out.put("ok", false);
            out.put("err", "the microphone failed: " + String.valueOf(e.getMessage()));
            return out;
        }
        long micOpenedAt = System.currentTimeMillis();
        short[] chunk = new short[1024];                 // 64ms at 16 kHz
        long deadline = micOpenedAt + Math.min(timeoutSeconds * 1000L, Audio.MAX_RECORD_MS);
        String why = "";
        try {
            while (done.getCount() > 0) {
                long now = System.currentTimeMillis();
                if (now > deadline) { why = "timeout"; break; }
                if (!speechStarted.get() && now - micOpenedAt > NO_SPEECH_MS) {
                    why = "no speech"; break;
                }
                long ended = speechEndedAt.get();
                if (ended > 0 && now - ended > COMPLETE_GRACE_MS) {
                    why = "speechEnd without speechComplete"; break;
                }
                int n = mic.read(chunk, 0, chunk.length);
                if (n <= 0) continue;
                ws.sendBinary(Audio.bytesOf(chunk, n));
            }
        } catch (IOException e) {
            error.compareAndSet(null, "send: " + e.getMessage());
        } finally {
            try { mic.stop(); } catch (Throwable ignored) {}
            try { mic.release(); } catch (Throwable ignored) {}
        }
        long recordedMs = System.currentTimeMillis() - micOpenedAt;

        // Tell the server we are finished and give it a moment to close the
        // turn, then take whatever is best: the completed turn, else the
        // last cumulative partial.
        try { ws.sendText(new JSONObject().put("type", "endStream").toString()); }
        catch (Exception ignored) {}
        try { done.await(COMPLETE_GRACE_MS, TimeUnit.MILLISECONDS); }
        catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
        ws.close();

        String text = complete.get();
        if (text == null || text.trim().isEmpty()) text = partial.get();
        text = text == null ? "" : text.trim();

        out.put("provider", "muse");
        out.put("mode", "stream");
        out.put("recorded_ms", recordedMs);
        out.put("total_ms", System.currentTimeMillis() - t0);
        if (firstPartialAt.get() > 0) {
            out.put("first_partial_ms", firstPartialAt.get() - micOpenedAt);
        }
        out.put("completed", complete.get() != null);
        if (!why.isEmpty()) out.put("ended_by", why);
        if (error.get() != null && text.isEmpty()) {
            out.put("ok", false);
            out.put("err", error.get());
            return out;
        }
        out.put("ok", !text.isEmpty());
        out.put("text", text);
        if (text.isEmpty()) out.put("err", "didn't catch that");
        if (error.get() != null) out.put("warning", error.get());
        return out;
    }

    // ------------------------------------------------------------ the wire

    static final class ApiFailure extends Exception {
        ApiFailure(String m) { super(m); }
    }

    /** The one-shot endpoint: a `request` JSON part and an `audio` WAV part. */
    private static String postMultipart(String url, String key, String requestJson,
                                        byte[] wav, int timeoutSeconds) throws Exception {
        String boundary = "----phonebridge" + System.nanoTime();
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setRequestMethod("POST");
            c.setConnectTimeout(10_000);
            c.setReadTimeout(Math.max(timeoutSeconds, 20) * 1000);
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            c.setRequestProperty("Authorization", "Bearer " + key);
            c.setRequestProperty("Accept", "application/json");
            c.setChunkedStreamingMode(0);
            try (OutputStream os = c.getOutputStream()) {
                os.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
                os.write(("Content-Disposition: form-data; name=\"request\"\r\n"
                          + "Content-Type: application/json\r\n\r\n")
                         .getBytes(StandardCharsets.UTF_8));
                os.write(requestJson.getBytes(StandardCharsets.UTF_8));
                os.write(("\r\n--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
                os.write(("Content-Disposition: form-data; name=\"audio\"; "
                          + "filename=\"turn.wav\"\r\n"
                          + "Content-Type: audio/wav\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                os.write(wav);
                os.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            }
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            String body = in == null ? "" : slurp(in);
            if (code >= 400) {
                String detail = body;
                try {
                    JSONObject o = new JSONObject(body);
                    JSONObject err = o.optJSONObject("error");
                    detail = err != null ? err.optString("message", body)
                                         : o.optString("message", o.optString("detail", body));
                } catch (Exception ignored) {}
                if (detail.length() > 400) detail = detail.substring(0, 400);
                throw new ApiFailure("muse HTTP " + code + ": " + detail);
            }
            return body;
        } finally {
            c.disconnect();
        }
    }

    private static String slurp(InputStream in) throws Exception {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        int n;
        while ((n = in.read(b)) > 0) o.write(b, 0, n);
        return o.toString(StandardCharsets.UTF_8.name());
    }

    static List<String> biasFor(String turnLang) {
        List<String> l = new ArrayList<>();
        if (Voices.LANG_HI.equals(turnLang)) {
            l.add("Hindi");
        } else if (Voices.LANG_MIX.equals(turnLang)) {
            l.add("Hindi");
            l.add("English");
        }
        return l;
    }
}
