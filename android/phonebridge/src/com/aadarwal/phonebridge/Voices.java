package com.aadarwal.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Which voice is speaking, and which ears are listening.
 *
 * There are two providers and they are good at different things:
 *
 *   android   on-device, no network, 11ms to first audio. ONE anonymous
 *             en-US voice, and en-US is the only language actually installed
 *             on this phone — hi-IN and en-IN are "supported", meaning
 *             downloadable, not present.
 *   sarvam    bulbul:v3 out (38 named speakers), saaras:v4 in (22 Indic
 *             languages + Indian English). Measured 2.16s and 1.72s against
 *             the live API — a real latency cost, paid for voice and reach.
 *
 * Neither is the right answer for every turn, so this holds both and makes
 * the choice explicit and persistent rather than compiled in.
 *
 * FALLBACK IS NOT SILENT. When the chosen provider fails, the other one
 * answers and the result says so in `fell_back_from`. A phone that quietly
 * changes voice mid-conversation because the network dropped is a phone
 * whose behaviour you cannot reason about — and the whole reason this class
 * exists is to stop the voice being a mystery.
 */
class Voices {

    static final String ANDROID = "android";
    static final String SARVAM = "sarvam";

    private static final String PREFS = "homi";
    // EARS AND MOUTH ARE SEPARATE CHOICES, and the right answers differ.
    //
    // The first version of this class had one `provider` covering both, which
    // was wrong and hid the asymmetry:
    //
    //   OUT  the phone's TTS is one anonymous en-US voice. Bulbul is 38 named
    //        speakers. The 1.6s is bought with something you cannot get
    //        locally at any latency, so sarvam is the default.
    //   IN   the phone's recogniser is on-device: no network leg, no
    //        per-request cost, nothing leaves the phone, and for English it is
    //        good. Saaras is a round trip to buy accuracy the local one
    //        already has. So ON-DEVICE is the default, and sarvam is for the
    //        thing local genuinely cannot do — Hindi and code-mixed speech,
    //        which this phone cannot recognise at all (installed: [en-US]).
    //
    // Paying a network round trip to transcribe English is spending latency
    // for nothing, which is exactly what one shared switch would have done.
    private static final String K_TTS = "voice_tts_provider";
    private static final String K_SPEAKER = "voice_speaker";
    private static final String K_LANG = "voice_turn_lang";

    /**
     * One per process. Both Bridge and Home used to build their own Speak and
     * Voice, which meant two TextToSpeech engines warmed in the same app and
     * two things that believed they owned the microphone. One owner.
     */
    private static volatile Voices instance;

    static Voices of(Context ctx) {
        Voices v = instance;
        if (v != null) return v;
        synchronized (Voices.class) {
            if (instance == null) instance = new Voices(ctx.getApplicationContext());
            return instance;
        }
    }

    /** Stages a caller can show while a turn is in flight. The Sarvam path
     *  has phases the on-device path does not — recording and transcribing
     *  are separate waits — and a UI that cannot see them can only show a
     *  spinner. */
    interface Progress {
        void at(String stage, String detail);
    }

    private final Context ctx;
    private final Speak androidSpeak;
    private final Voice androidVoice;
    private final Sarvam sarvam;

    private Voices(Context ctx) {
        this.ctx = ctx;
        this.androidSpeak = new Speak(ctx);
        this.androidVoice = new Voice(ctx);
        this.sarvam = new Sarvam(ctx);
    }

    // ---------------------------------------------------------- selection

    private SharedPreferences prefs() {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /**
     * Which voice SPEAKS. Defaults to sarvam when a key is present — the 38
     * speakers are the whole reason to pay the latency — and to android
     * otherwise, so a device with no key behaves exactly as it did before
     * this class existed instead of failing every turn.
     */
    String ttsProvider() {
        String stored = prefs().getString(K_TTS, null);
        if (stored == null) return sarvam.available() ? SARVAM : ANDROID;
        if (SARVAM.equals(stored)) return sarvam.available() ? SARVAM : ANDROID;
        return ANDROID;
    }

    // ------------------------------------------------- the language of a turn
    //
    // NOT a provider picker. Nobody stands there thinking "I would like to use
    // Saaras now" — they think "I am about to speak Hindi", and the provider
    // is a consequence of that, not a decision of its own. So the language is
    // the control and everything else is derived from it:
    //
    //   EN    on-device. Free, instant, private, and good at English. The
    //         local recogniser has en-US installed and nothing else.
    //   HI    saaras, hi-IN. The phone CANNOT do this — hi-IN is listed as
    //         "supported", meaning downloadable, and is not present.
    //   MIX   saaras in `codemix` mode, which is the mode built for Hindi and
    //         English inside one sentence. Sending `transcribe` at Hinglish
    //         is asking the wrong question of a model that has the right one.
    //
    // The language also picks what Bulbul speaks BACK, because answering a
    // Hindi question in an American accent is its own kind of wrong.

    static final String LANG_EN = "en";
    static final String LANG_HI = "hi";
    static final String LANG_MIX = "mix";

    String turnLang() {
        String l = prefs().getString(K_LANG, LANG_EN);
        if (LANG_HI.equals(l) || LANG_MIX.equals(l)) {
            // Without a key there is no Hindi at all on this device, so fall
            // back rather than record a turn nothing can transcribe.
            return sarvam.available() ? l : LANG_EN;
        }
        return LANG_EN;
    }

    boolean setTurnLang(String l) {
        if (!LANG_EN.equals(l) && !LANG_HI.equals(l) && !LANG_MIX.equals(l)) return false;
        if (!LANG_EN.equals(l) && !sarvam.available()) return false;
        prefs().edit().putString(K_LANG, l).apply();
        return true;
    }

    /** Derived, never stored: English listens locally, everything else cannot. */
    String sttProvider() {
        return LANG_EN.equals(turnLang()) ? ANDROID : SARVAM;
    }

    /** null lets Saaras detect it, which is what codemix wants. */
    private String sttLanguageCode() {
        return LANG_HI.equals(turnLang()) ? "hi-IN" : null;
    }

    private String sttMode() {
        return LANG_MIX.equals(turnLang()) ? "codemix" : "transcribe";
    }

    /** What Bulbul speaks back when we have nothing but the setting to go on. */
    String ttsLanguage() {
        return LANG_HI.equals(turnLang()) ? "hi-IN" : Sarvam.DEFAULT_LANGUAGE;
    }

    /**
     * What Bulbul should speak THIS answer in — read off the answer itself.
     *
     * The turn's language says what you SPOKE, and that is not reliably what
     * comes back. Observed on the device: with HINGLISH selected, one turn was
     * asked in Hindi and answered "मैं बढ़िया हूँ, धन्यवाद!", and the setting
     * would have handed that Devanagari to an en-IN voice; the next turn was
     * asked in English and answered in English, where hi-IN would have been
     * just as wrong. No fixed setting is right for both, because the answer's
     * language is chosen by the model, not by the person.
     *
     * So look at the text. Devanagari means Hindi; anything else takes the
     * turn's own default. Cheap, and it cannot drift out of agreement with
     * what is actually about to be spoken.
     */
    String ttsLanguageFor(String text) {
        if (text != null) {
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                // Escapes, not literals. A display string that arrives
                // mis-decoded looks wrong and gets noticed; a COMPARISON
                // that arrives mis-decoded silently stops matching.
                if (c >= '\u0900' && c <= '\u097F') return "hi-IN";
            }
        }
        return ttsLanguage();
    }

    boolean setTtsProvider(String p) {
        if (!ANDROID.equals(p) && !SARVAM.equals(p)) return false;
        if (SARVAM.equals(p) && !sarvam.available()) return false;
        prefs().edit().putString(K_TTS, p).apply();
        return true;
    }

    String speaker() {
        return prefs().getString(K_SPEAKER, Sarvam.DEFAULT_SPEAKER);
    }

    boolean setSpeaker(String s) {
        if (s == null || !Arrays.asList(Sarvam.SPEAKERS).contains(s)) return false;
        prefs().edit().putString(K_SPEAKER, s).apply();
        return true;
    }

    /** Whether a Sarvam key is present at all. The voice picker asks, so it
     *  can say WHY the 38 speakers are missing instead of just not showing
     *  them. */
    boolean sarvamConfigured() {
        return sarvam.available();
    }

    /** The personas the ACTIVE provider offers. Android exposes one unnamed
     *  system voice through this bridge; Sarvam exposes 38. */
    List<String> speakers() {
        return SARVAM.equals(ttsProvider()) ? Sarvam.speakers() : new ArrayList<String>();
    }

    // -------------------------------------------------------------- speak

    Map<String, Object> say(String text, int timeoutSeconds) {
        return say(text, timeoutSeconds, null);
    }

    Map<String, Object> say(String text, int timeoutSeconds, Progress p) {
        String want = ttsProvider();
        if (SARVAM.equals(want)) {
            if (p != null) p.at("SPEAKING", "bulbul " + speaker());
            Map<String, Object> r = sarvam.say(text, speaker(), ttsLanguageFor(text), timeoutSeconds);
            if (Boolean.TRUE.equals(r.get("ok"))) return r;
            Log.w(Listener.TAG, "bulbul failed, falling back: " + r.get("err"));
            if (p != null) p.at("SPEAKING", "on-device (bulbul failed)");
            Map<String, Object> back = androidSpeak.say(text, timeoutSeconds);
            back.put("provider", ANDROID);
            back.put("fell_back_from", SARVAM);
            back.put("fell_back_because", String.valueOf(r.get("err")));
            return back;
        }
        if (p != null) p.at("SPEAKING", "on-device");
        Map<String, Object> r = androidSpeak.say(text, timeoutSeconds);
        r.put("provider", ANDROID);
        return r;
    }

    // ------------------------------------------------------------- listen

    Map<String, Object> listen(int timeoutSeconds) {
        return listen(timeoutSeconds, null);
    }

    Map<String, Object> listen(int timeoutSeconds, Progress p) {
        String want = sttProvider();
        if (SARVAM.equals(want)) {
            if (p != null) p.at("LISTENING", "saaras");
            Map<String, Object> r = sarvam.listen(timeoutSeconds, sttLanguageCode(), sttMode());
            // "didn't catch that" is an ANSWER, not a provider failure —
            // falling back to the other recogniser and re-recording would
            // make the person say it twice for no reason.
            if (Boolean.TRUE.equals(r.get("ok"))) return r;
            String err = String.valueOf(r.get("err"));
            if (err.startsWith("didn't catch")) return r;
            Log.w(Listener.TAG, "saaras failed, falling back: " + err);
            if (p != null) p.at("LISTENING", "on-device (saaras failed)");
            Map<String, Object> back = androidVoice.listen(timeoutSeconds);
            back.put("provider", ANDROID);
            back.put("fell_back_from", SARVAM);
            back.put("fell_back_because", err);
            return back;
        }
        if (p != null) p.at("LISTENING", "on-device");
        Map<String, Object> r = androidVoice.listen(timeoutSeconds);
        r.put("provider", ANDROID);
        return r;
    }

    void stop() {
        sarvam.stop();
        androidSpeak.stop();
    }

    // ------------------------------------------------------------- status

    Map<String, Object> status() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("tts_provider", ttsProvider());
        out.put("stt_provider", sttProvider());
        out.put("speaker", speaker());
        out.put("turn_lang", turnLang());
        out.put("tts_language", ttsLanguage());
        out.put("stt_mode", sttMode());
        out.put("sarvam_configured", sarvam.available());
        out.put("speakers", speakers());
        Map<String, Object> a = new LinkedHashMap<>(androidSpeak.status());
        out.put("android_tts", a);
        out.put("android_stt", androidVoice.status());
        out.put("sarvam", sarvam.status());
        return out;
    }

    /** The on-device recogniser, for callers that specifically want it
     *  (voice_download reaches past the abstraction on purpose — it is a
     *  property of the Android engine, not of "a voice provider"). */
    Voice androidVoice() {
        return androidVoice;
    }
}
