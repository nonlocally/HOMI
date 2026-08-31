package com.aadarwal.phonebridge;

import android.content.Context;
import android.util.Log;

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
 * xAI as a voice: Grok's TTS, 28 voices, and the fastest thing here.
 *
 * Measured against the live API on the same network as the Bulbul numbers:
 *
 *   xAI /v1/tts        0.77s
 *   Bulbul :v3         2.16s
 *
 * Nearly three times faster for a comparable sentence, which matters more than
 * it sounds: first-audio is the whole perceived responsiveness of a voice
 * interface, and 0.77s is the difference between "it answered" and "it is
 * thinking". Every voice here is tagged `multilingual` by the API rather than
 * pinned to a locale, so unlike Bulbul there is no language code to get right.
 *
 * TTS ONLY. xAI's speech-to-text lives in its realtime voice-to-voice stack,
 * which is a browser/WebRTC architecture and not a request-response API this
 * can call — so the ears stay on-device or on Saaras. Wiring half a provider
 * and pretending it is whole is how you get a fallback that never fires.
 */
class Xai {

    static final String TTS_URL = "https://api.x.ai/v1/tts";
    static final String VOICES_URL = "https://api.x.ai/v1/tts/voices";

    /**
     * The 28 voice ids the API itself returned, in its own order. Read from
     * /v1/tts/voices rather than copied from a docs page, for the same reason
     * the Bulbul list came out of an API error: the service is the only
     * source that cannot drift from what it will actually accept.
     */
    static final String[] VOICES = {
        "altair", "ara", "atlas", "aurora", "carina", "castor", "celeste",
        "cosmo", "eve", "helios", "helix", "iris", "kepler", "leo", "liora",
        "lumen", "luna", "lux", "naksh", "orion", "perseus", "rex", "rigel",
        "sal", "sirius", "ursa", "zagan", "zenith",
    };

    static final String DEFAULT_VOICE = "eve";

    /** Same place, same reasoning, as the sarvam key and the bridge token:
     *  the app's external files dir, which shell writes and no other app
     *  can read. Never compiled into the APK. */
    private static final String KEY_FILE = "xai_key";

    private final Context ctx;
    private final Audio audio;
    private volatile String key;

    Xai(Context ctx, Audio audio) {
        this.ctx = ctx;
        this.audio = audio;
    }

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
                Log.w(Listener.TAG, "could not read the xai key", e);
            }
            return key;
        }
    }

    boolean available() {
        return key() != null;
    }

    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("provider", "xai");
        out.put("configured", available());
        out.put("voices", new ArrayList<>(Arrays.asList(VOICES)));
        out.put("stt", false);      // realtime-only; see the class comment
        if (!available()) {
            out.put("detail", "no key at <externalFilesDir>/" + KEY_FILE);
        }
        return out;
    }

    /**
     * Speak it.
     *
     * xAI returns RAW AUDIO BYTES, not the base64-in-JSON that Sarvam uses —
     * so there is nothing to decode, and a JSON body coming back means an
     * error rather than a result.
     *
     * We ask for wav/24000 explicitly. The default is not documented to be
     * stable, and {@link Audio.Pcm} would cope, but a container we chose is
     * one fewer thing that can quietly change under us.
     */
    Map<String, Object> say(String text, String voice, int timeoutSeconds) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (text == null || text.trim().isEmpty()) {
            out.put("ok", false);
            out.put("err", "refusing to speak an empty string");
            return out;
        }
        String k = key();
        if (k == null) {
            out.put("ok", false);
            out.put("err", "xai is not configured (no key on this device)");
            return out;
        }
        audio.begin();
        long t0 = System.currentTimeMillis();
        byte[] wav;
        try {
            JSONObject fmt = new JSONObject();
            fmt.put("codec", "wav");
            fmt.put("sample_rate", 24000);
            JSONObject body = new JSONObject();
            body.put("text", text);
            body.put("voice_id", voice == null || voice.isEmpty() ? DEFAULT_VOICE : voice);
            // Every xAI voice reports language "multilingual", so this is a
            // hint rather than a selector — unlike Bulbul, where the wrong
            // target_language_code reads Devanagari in an American accent.
            body.put("language", "en");
            body.put("output_format", fmt);
            wav = post(TTS_URL, k, body.toString(), timeoutSeconds);
        } catch (ApiFailure e) {
            out.put("ok", false);
            out.put("err", e.getMessage());
            return out;
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "xai tts failed: " + String.valueOf(e.getMessage()));
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
            out.put("speaker", voice == null || voice.isEmpty() ? DEFAULT_VOICE : voice);
            out.put("provider", "xai");
        } catch (Exception e) {
            out.put("ok", false);
            out.put("err", "could not play xai audio: " + e.getMessage());
        }
        return out;
    }

    static final class ApiFailure extends Exception {
        ApiFailure(String m) { super(m); }
    }

    private static byte[] post(String url, String key, String body, int timeoutSeconds)
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
            c.setRequestProperty("Authorization", "Bearer " + key);
            try (OutputStream os = c.getOutputStream()) {
                os.write(payload);
            }
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            byte[] data = in == null ? new byte[0] : slurp(in);
            if (code >= 400) {
                throw new ApiFailure("xai HTTP " + code + ": " + detail(data));
            }
            // A JSON body on a 200 is xAI telling us something other than
            // audio — surface it as itself rather than handing the bytes to a
            // WAV parser that will report "not a RIFF/WAVE payload" and blame
            // the wrong layer.
            String ctype = String.valueOf(c.getContentType()).toLowerCase();
            if (ctype.contains("json")) {
                throw new ApiFailure("xai returned JSON, not audio: " + detail(data));
            }
            if (data.length == 0) throw new ApiFailure("xai returned no audio");
            return data;
        } finally {
            c.disconnect();
        }
    }

    private static String detail(byte[] data) {
        String s = new String(data, StandardCharsets.UTF_8).trim();
        try {
            JSONObject o = new JSONObject(s);
            JSONObject err = o.optJSONObject("error");
            if (err != null) s = err.optString("message", s);
            else s = o.optString("message", o.optString("detail", s));
        } catch (Exception ignored) {}
        return s.length() > 400 ? s.substring(0, 400) : s;
    }

    private static byte[] slurp(InputStream in) throws Exception {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        int n;
        while ((n = in.read(b)) > 0) o.write(b, 0, n);
        return o.toByteArray();
    }

    static List<String> voices() {
        return new ArrayList<>(Arrays.asList(VOICES));
    }
}
