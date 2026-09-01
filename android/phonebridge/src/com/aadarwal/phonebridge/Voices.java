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
 *   sarvam    bulbul:v3 out (38 named speakers). 2.9s on the phone. Out only.
 *   xai       grok tts, 28 multilingual voices, 0.8s on the phone. Out only.
 *   muse      Meta's Voice Transcribe: the ears for Hindi and code-mixed
 *             speech, one-shot or streaming. In only. Replaced Saaras 2026-09-01.
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
    static final String XAI = "xai";
    static final String MUSE = "muse";

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
    // A voice name is remembered PER PROVIDER. Switching from bulbul to grok
    // and back should return you to the bulbul voice you had, not to a
    // default — and a single slot cannot hold both, because "eve" is not a
    // bulbul speaker and "anand" is not an xAI voice.
    private static final String K_SPK_SARVAM = "voice_speaker";
    private static final String K_SPK_XAI = "voice_speaker_xai";
    private static final String K_LANG = "voice_turn_lang";
    // Muse streams by preference. Off means record-then-POST, which is the
    // proven shape; on means partials while you talk and Muse's own
    // endpointer. Defaults OFF until the stream path has been proven on a
    // real turn on this device — a provider landed too fast crashed this
    // app once already today.
    private static final String K_STREAM = "voice_muse_stream";

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
    private final Audio audio;
    private final Sarvam sarvam;
    private final Xai xai;
    private final Muse muse;

    private Voices(Context ctx) {
        this.ctx = ctx;
        this.androidSpeak = new Speak(ctx);
        this.androidVoice = new Voice(ctx);
        // One player shared by both cloud voices, so only one thing can be
        // speaking and `shut_up` silences whichever it is.
        this.audio = new Audio();
        this.sarvam = new Sarvam(ctx, audio);
        this.xai = new Xai(ctx, audio);
        this.muse = new Muse(ctx, audio);
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
        if (XAI.equals(stored) && xai.available()) return XAI;
        if (SARVAM.equals(stored) && sarvam.available()) return SARVAM;
        if (ANDROID.equals(stored)) return ANDROID;
        // No stored choice, or a choice whose key has since gone. Prefer the
        // cloud voices — they are the reason to have this class — and fall
        // back to on-device rather than failing every turn.
        if (sarvam.available()) return SARVAM;
        if (xai.available()) return XAI;
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
    //   HI    muse, languageBias ["Hindi"]. The phone CANNOT do this — hi-IN
    //         is listed as "supported", meaning downloadable, and not present.
    //   MIX   muse, languageBias ["Hindi", "English"]. Code-switching inside
    //         a sentence is native to Muse, so this is a hint, not a mode.
    //
    // The language also picks what Bulbul speaks BACK, because answering a
    // Hindi question in an American accent is its own kind of wrong.

    static final String LANG_EN = "en";
    static final String LANG_HI = "hi";
    static final String LANG_MIX = "mix";

    String turnLang() {
        String l = prefs().getString(K_LANG, LANG_EN);
        if (LANG_HI.equals(l) || LANG_MIX.equals(l)) {
            // Without a Muse key there is no Hindi at all on this device, so
            // fall back rather than record a turn nothing can transcribe.
            return muse.available() ? l : LANG_EN;
        }
        return LANG_EN;
    }

    boolean setTurnLang(String l) {
        if (!LANG_EN.equals(l) && !LANG_HI.equals(l) && !LANG_MIX.equals(l)) return false;
        if (!LANG_EN.equals(l) && !muse.available()) return false;
        prefs().edit().putString(K_LANG, l).apply();
        return true;
    }

    /** Derived, never stored: English listens locally, everything else cannot. */
    String sttProvider() {
        return LANG_EN.equals(turnLang()) ? ANDROID : MUSE;
    }

    boolean streaming() {
        return prefs().getBoolean(K_STREAM, false);
    }

    void setStreaming(boolean on) {
        prefs().edit().putBoolean(K_STREAM, on).apply();
    }

    boolean museConfigured() {
        return muse.available();
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
        if (SARVAM.equals(p) && !sarvam.available()) return false;
        if (XAI.equals(p) && !xai.available()) return false;
        if (!ANDROID.equals(p) && !SARVAM.equals(p) && !XAI.equals(p)) return false;
        prefs().edit().putString(K_TTS, p).apply();
        return true;
    }

    /** The voice of the ACTIVE provider. Android has one, and it is unnamed. */
    String speaker() {
        String p = ttsProvider();
        if (SARVAM.equals(p)) return prefs().getString(K_SPK_SARVAM, Sarvam.DEFAULT_SPEAKER);
        if (XAI.equals(p)) return prefs().getString(K_SPK_XAI, Xai.DEFAULT_VOICE);
        return "";
    }

    /**
     * Set the voice BY NAME, and move the provider to whichever owns it.
     *
     * The two catalogues are disjoint, so a name identifies its provider
     * without being told — which is what lets the picker be one flat list of
     * voices instead of a provider screen followed by a voice screen. Nobody
     * choosing how their phone should sound wants to pick a vendor first.
     */
    boolean setSpeaker(String s) {
        if (s == null || s.isEmpty()) return false;
        if (Arrays.asList(Sarvam.SPEAKERS).contains(s)) {
            if (!sarvam.available()) return false;
            prefs().edit().putString(K_SPK_SARVAM, s).putString(K_TTS, SARVAM).apply();
            return true;
        }
        if (Arrays.asList(Xai.VOICES).contains(s)) {
            if (!xai.available()) return false;
            prefs().edit().putString(K_SPK_XAI, s).putString(K_TTS, XAI).apply();
            return true;
        }
        return false;
    }

    /** Whether a Sarvam key is present at all. The voice picker asks, so it
     *  can say WHY the 38 speakers are missing instead of just not showing
     *  them. */
    boolean sarvamConfigured() {
        return sarvam.available();
    }

    boolean xaiConfigured() {
        return xai.available();
    }

    /** The personas the ACTIVE provider offers. Android exposes one unnamed
     *  system voice through this bridge; Sarvam exposes 38. */
    List<String> speakers() {
        String p = ttsProvider();
        if (SARVAM.equals(p)) return Sarvam.speakers();
        if (XAI.equals(p)) return Xai.voices();
        return new ArrayList<String>();
    }

    // -------------------------------------------------------------- speak

    Map<String, Object> say(String text, int timeoutSeconds) {
        return say(text, timeoutSeconds, null);
    }

    Map<String, Object> say(String text, int timeoutSeconds, Progress p) {
        String want = ttsProvider();
        if (XAI.equals(want)) {
            if (p != null) p.at("SPEAKING", "grok " + speaker());
            Map<String, Object> r = xai.say(text, speaker(), timeoutSeconds);
            if (Boolean.TRUE.equals(r.get("ok"))) return r;
            Log.w(Listener.TAG, "grok failed, falling back: " + r.get("err"));
            if (p != null) p.at("SPEAKING", "on-device (grok failed)");
            Map<String, Object> back = androidSpeak.say(text, timeoutSeconds);
            back.put("provider", ANDROID);
            back.put("fell_back_from", XAI);
            back.put("fell_back_because", String.valueOf(r.get("err")));
            return back;
        }
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
        if (MUSE.equals(want)) {
            Map<String, Object> r = muse.listen(timeoutSeconds, Muse.biasFor(turnLang()),
                                                streaming(), p);
            // "didn't catch that" is an ANSWER, not a provider failure —
            // falling back to the other recogniser and re-recording would
            // make the person say it twice for no reason.
            if (Boolean.TRUE.equals(r.get("ok"))) return r;
            String err = String.valueOf(r.get("err"));
            if (err.startsWith("didn't catch")) return r;
            Log.w(Listener.TAG, "muse failed, falling back: " + err);
            if (p != null) p.at("LISTENING", "on-device (muse failed)");
            Map<String, Object> back = androidVoice.listen(timeoutSeconds);
            back.put("provider", ANDROID);
            back.put("fell_back_from", MUSE);
            back.put("fell_back_because", err);
            return back;
        }
        if (p != null) p.at("LISTENING", "on-device");
        Map<String, Object> r = androidVoice.listen(timeoutSeconds);
        r.put("provider", ANDROID);
        return r;
    }

    void stop() {
        audio.stop();          // whichever cloud voice is mid-utterance
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
        out.put("stt_streaming", streaming());
        out.put("muse_configured", muse.available());
        out.put("sarvam_configured", sarvam.available());
        out.put("xai_configured", xai.available());
        out.put("speakers", speakers());
        Map<String, Object> a = new LinkedHashMap<>(androidSpeak.status());
        out.put("android_tts", a);
        out.put("android_stt", androidVoice.status());
        out.put("sarvam", sarvam.status());
        out.put("xai", xai.status());
        out.put("muse", muse.status());
        return out;
    }

    /** The on-device recogniser, for callers that specifically want it
     *  (voice_download reaches past the abstraction on purpose — it is a
     *  property of the Android engine, not of "a voice provider"). */
    Voice androidVoice() {
        return androidVoice;
    }
}
