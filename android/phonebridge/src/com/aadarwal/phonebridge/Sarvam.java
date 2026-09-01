package com.aadarwal.phonebridge;

import android.content.Context;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Sarvam as a voice: Bulbul v3, out only.
 *
 * Saaras was the ears here until 2026-09-01, when Muse Voice Transcribe took
 * that job — see {@link Muse}. The capture code that lived here moved to
 * {@link Audio}, where it always belonged.
 *
 * The device already has both halves locally — Android's on-device recogniser
 * and a warm platform TTS at 11ms to first audio — so this is NOT a latency
 * win and should not pretend to be. Measured against the live API from this
 * machine: Bulbul 2.16s for a short sentence, Saaras 1.72s for a 3s clip.
 * What it buys instead is (a) 38 named speakers against one anonymous
 * `en-US-language`, and (b) 22 Indic languages against the ONE the phone
 * actually has installed — voice_status reports installed:[en-US] and
 * everything else, en-IN and hi-IN included, merely "supported".
 *
 * So both providers stay wired and the choice is explicit. See {@link Voices}.
 *
 * Direct from the phone, not via the board, because every hop is latency we
 * are already spending too much of, and because only this path can move to
 * Sarvam's streaming WebSockets later without being rearchitected.
 */
class Sarvam {

    static final String TTS_URL = "https://api.sarvam.ai/text-to-speech";

    static final String TTS_MODEL = "bulbul:v3";

    /**
     * The 38 speakers bulbul:v3 accepts, in the order the API itself lists
     * them. NOT a guess and not copied from a docs page: this is the set the
     * API returned in its own 400 when handed a v2 speaker, which is the only
     * source that cannot drift from what the service will actually accept.
     *
     * v2's speakers (anushka, manisha, vidya, arya, abhilash, karun, hitesh)
     * are REJECTED by v3 — the first request this code ever made failed that
     * way. Do not mix the lists.
     */
    static final String[] SPEAKERS = {
        "aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja",
        "rohan", "simran", "kavya", "amit", "dev", "ishita", "shreya",
        "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan",
        "shubh", "advait", "anand", "tanya", "tarun", "sunny", "mani",
        "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan",
        "soham", "rupali", "niharika",
    };

    static final String DEFAULT_SPEAKER = "anand";
    static final String DEFAULT_LANGUAGE = "en-IN";

    /**
     * Where the key lives: the app's own external files dir, exactly like the
     * bridge token. The device shell can read it, other apps cannot, and the
     * installer plants it there. It is NOT compiled in — a key in the APK is
     * a key in every backup and every copy of the build output.
     */
    private static final String KEY_FILE = "sarvam_key";


    private final Context ctx;
    private final Audio audio;
    private volatile String key;

    Sarvam(Context ctx, Audio audio) {
        this.ctx = ctx;
        this.audio = audio;
    }

    // ------------------------------------------------------------- the key

    /** The key, or null. Read once and cached; a missing key is a normal
     *  state (the provider is simply unavailable), not an error to throw. */
    String key() {
        if (key != null) return key;
        synchronized (this) {
            if (key != null) return key;
            try {
                File dir = ctx.getExternalFilesDir(null);
                if (dir == null) return null;
                File f = new File(dir, KEY_FILE);
                if (!f.exists()) return null;
                byte[] b = new byte[256];
                int n;
                try (FileInputStream in = new FileInputStream(f)) {
                    n = in.read(b);
                }
                if (n <= 0) return null;
                String s = new String(b, 0, n, StandardCharsets.UTF_8).trim();
                key = s.isEmpty() ? null : s;
            } catch (Exception e) {
                Log.w(Listener.TAG, "could not read the sarvam key", e);
            }
            return key;
        }
    }

    boolean available() {
        return key() != null;
    }

    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        boolean have = available();
        out.put("provider", "sarvam");
        out.put("configured", have);
        out.put("tts_model", TTS_MODEL);
        out.put("stt", false);   // ears moved to Muse, 2026-09-01
        out.put("speakers", new ArrayList<>(Arrays.asList(SPEAKERS)));
        if (!have) {
            out.put("detail", "no key at <externalFilesDir>/" + KEY_FILE);
        }
        return out;
    }

    // -------------------------------------------------------------- speak

    /**
     * Bulbul v3, spoken through AudioTrack.
     *
     * Reports first_audio_ms and total_ms with the same meaning Speak gives
     * them, so the two providers are comparable on the one axis that matters
     * to someone waiting. Here first-audio is dominated by the API call —
     * there is nothing warm to exploit — which is exactly the number that
     * will justify moving to the streaming endpoint later.
     */
    Map<String, Object> say(String text, String speaker, String language,
                            int timeoutSeconds) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (text == null || text.trim().isEmpty()) {
            out.put("ok", false);
            out.put("err", "refusing to speak an empty string");
            return out;
        }
        String k = key();
        if (k == null) {
            out.put("ok", false);
            out.put("err", "sarvam is not configured (no key on this device)");
            return out;
        }
        audio.begin();
        long t0 = System.currentTimeMillis();
        byte[] wav;
        try {
            JSONObject body = new JSONObject();
            body.put("text", text);
            body.put("target_language_code",
                     language == null || language.isEmpty() ? DEFAULT_LANGUAGE : language);
            body.put("model", TTS_MODEL);
            body.put("speaker",
                     speaker == null || speaker.isEmpty() ? DEFAULT_SPEAKER : speaker);

            String resp = postJson(TTS_URL, k, body.toString(), timeoutSeconds);
            JSONObject o = new JSONObject(resp);
            JSONArray audios = o.optJSONArray("audios");
            if (audios == null || audios.length() == 0) {
                out.put("ok", false);
                out.put("err", "sarvam returned no audio");
                return out;
            }
            wav = Base64.decode(audios.getString(0), Base64.DEFAULT);
        } catch (ApiFailure e) {
            out.put("ok", false);
            out.put("err", e.getMessage());
            return out;
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "bulbul failed: " + String.valueOf(e.getMessage()));
            return out;
        }

        long fetched = System.currentTimeMillis() - t0;
        try {
            Audio.Pcm pcm = Audio.Pcm.parse(wav);
            long firstAudio = audio.play(pcm, t0);
            out.put("ok", true);
            out.put("first_audio_ms", firstAudio);
            out.put("total_ms", System.currentTimeMillis() - t0);
            out.put("fetch_ms", fetched);
            out.put("chars", text.length());
            out.put("speaker", speaker == null || speaker.isEmpty()
                               ? DEFAULT_SPEAKER : speaker);
            // Report the language actually SENT. It is chosen per-utterance
            // from the answer's own script, so without this there is no way
            // to check the choice was right except by listening to it.
            out.put("language", language == null || language.isEmpty()
                                ? DEFAULT_LANGUAGE : language);
            out.put("provider", "sarvam");
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "could not play bulbul audio: " + e.getMessage());
        }
        return out;
    }




    // ------------------------------------------------------------ the wire

    /** Sarvam said no, with its own words kept. */
    static final class ApiFailure extends Exception {
        ApiFailure(String m) { super(m); }
    }

    private static String postJson(String url, String key, String body, int timeoutSeconds)
            throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            c.setRequestMethod("POST");
            c.setConnectTimeout(10_000);
            c.setReadTimeout(Math.max(timeoutSeconds, 15) * 1000);
            c.setDoOutput(true);
            c.setFixedLengthStreamingMode(payload.length);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("api-subscription-key", key);
            try (OutputStream os = c.getOutputStream()) {
                os.write(payload);
            }
            return read(c);
        } finally {
            c.disconnect();
        }
    }



    /**
     * Read the response, and on a failure carry SARVAM's own message up.
     *
     * The first request this code made came back 400 "Speaker 'anushka' is
     * not compatible with model bulbul:v3", which named the exact problem and
     * listed every valid speaker. A generic "TTS failed" would have thrown
     * that away and left someone guessing.
     */
    private static String read(HttpURLConnection c) throws Exception {
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String body = in == null ? "" : slurp(in);
        if (code >= 400) {
            String detail = body;
            try {
                JSONObject o = new JSONObject(body);
                JSONObject err = o.optJSONObject("error");
                if (err != null) detail = err.optString("message", body);
                else detail = o.optString("message", body);
            } catch (Exception ignored) {}
            if (detail.length() > 400) detail = detail.substring(0, 400);
            throw new ApiFailure("sarvam HTTP " + code + ": " + detail);
        }
        return body;
    }

    private static String slurp(InputStream in) throws Exception {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        int n;
        while ((n = in.read(b)) > 0) o.write(b, 0, n);
        return o.toString(StandardCharsets.UTF_8.name());
    }

    static List<String> speakers() {
        return new ArrayList<>(Arrays.asList(SPEAKERS));
    }
}
