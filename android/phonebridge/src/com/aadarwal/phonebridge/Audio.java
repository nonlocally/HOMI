package com.aadarwal.phonebridge;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Playing a WAV that arrived over HTTP, and knowing when it finished.
 *
 * Extracted when xAI became a second cloud voice: both it and Bulbul return a
 * RIFF/WAVE body that has to be parsed, pushed at an AudioTrack, and drained
 * before release. That is the same problem twice, and the interesting part —
 * the container parsing — is exactly the part it would be careless to fork.
 *
 * ONE instance per app, held by {@link Voices}. Two providers each holding
 * their own player is two things that can talk at once, and `shut_up` would
 * only silence one of them.
 */
class Audio {

    private volatile AudioTrack track;
    private final AtomicBoolean cancelled = new AtomicBoolean(false);

    /** Call before an utterance; clears a cancel left over from the last one. */
    void begin() {
        cancelled.set(false);
    }

    /**
     * Blocking playback. Returns ms from {@code t0} to the first audible
     * sample — which is the number a person actually experiences as
     * responsiveness, and is not the same as how long the whole thing took.
     */
    long play(Pcm pcm, long t0) {
        int min = AudioTrack.getMinBufferSize(
            pcm.rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufSize = Math.max(min, pcm.data.length);
        AudioTrack t = new AudioTrack.Builder()
            .setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build())
            .setAudioFormat(new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(pcm.rate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .build())
            .setBufferSizeInBytes(bufSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build();
        track = t;
        long firstAudio = 0;
        try {
            t.play();
            firstAudio = System.currentTimeMillis() - t0;
            int off = 0;
            while (off < pcm.data.length && !cancelled.get()) {
                int n = t.write(pcm.data, off, Math.min(8192, pcm.data.length - off));
                if (n <= 0) break;
                off += n;
            }
            // Let the buffer drain. Releasing straight after the last write
            // cuts the tail off mid-word — the audible version of a function
            // that returns before its work is done.
            if (!cancelled.get()) {
                long tailMs = (long) (1000.0 * (pcm.data.length / 2.0) / pcm.rate);
                long deadline = System.currentTimeMillis() + Math.min(tailMs + 500, 60_000);
                while (System.currentTimeMillis() < deadline && !cancelled.get()
                       && t.getPlaybackHeadPosition() < pcm.data.length / 2) {
                    try { Thread.sleep(40); } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
        } finally {
            try { t.stop(); } catch (Throwable ignored) {}
            try { t.release(); } catch (Throwable ignored) {}
            if (track == t) track = null;
        }
        return firstAudio;
    }

    void stop() {
        cancelled.set(true);
        AudioTrack t = track;
        if (t != null) {
            try { t.pause(); } catch (Throwable ignored) {}
            try { t.flush(); } catch (Throwable ignored) {}
        }
    }

    // ------------------------------------------------------------- capture
    //
    // Moved here from Sarvam when Muse replaced Saaras as the ears. Capture
    // was never Sarvam's; it was the microphone's, and two cloud recognisers
    // in a row wanting "mono 16-bit PCM at 16 kHz" made that obvious.

    static final int RECORD_RATE = 16_000;

    /** Muse's one-shot endpoint allows ten minutes. Nobody wants a
     *  ten-minute turn; this is a safety stop, not a feature. */
    static final int MAX_RECORD_MS = 28_000;

    /**
     * Endpointing for the record-then-send path, and it is a HEURISTIC: an
     * RMS threshold and a silence timer, invented rather than trained. It is
     * what the one-shot HTTP path has to live with, because a file API cannot
     * tell you when someone stopped talking. The streaming path does not use
     * it at all — Muse's own endpointer runs on the server and is the reason
     * to prefer that path.
     */
    private static final int SILENCE_MS = 900;
    private static final int MIN_SPEECH_MS = 300;
    private static final double SILENCE_RMS = 550.0;

    static final class Recorded {
        byte[] pcm;
        boolean heardSpeech;
        long ms;
    }

    /** A configured, started microphone. The caller owns stop()/release(). */
    static android.media.AudioRecord openMic() {
        int min = android.media.AudioRecord.getMinBufferSize(
            RECORD_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (min <= 0) throw new IllegalStateException("no usable microphone buffer size");
        int bufSize = Math.max(min, RECORD_RATE / 2);
        android.media.AudioRecord r = new android.media.AudioRecord(
            android.media.MediaRecorder.AudioSource.VOICE_RECOGNITION, RECORD_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufSize);
        if (r.getState() != android.media.AudioRecord.STATE_INITIALIZED) {
            try { r.release(); } catch (Throwable ignored) {}
            throw new IllegalStateException("microphone unavailable (permission or in use)");
        }
        r.startRecording();
        return r;
    }

    /** Little-endian bytes for a run of samples. */
    static byte[] bytesOf(short[] chunk, int n) {
        byte[] bytes = new byte[n * 2];
        ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(chunk, 0, n);
        return bytes;
    }

    static double rms(short[] chunk, int n) {
        double sum = 0;
        for (int i = 0; i < n; i++) sum += (double) chunk[i] * chunk[i];
        return Math.sqrt(sum / n);
    }

    /** Record until the person stops (by the heuristic above), or maxMs. */
    static Recorded record(int maxMs) throws Exception {
        android.media.AudioRecord r = openMic();
        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream(RECORD_RATE * 4);
        Recorded out = new Recorded();
        short[] chunk = new short[1024];
        long start = System.currentTimeMillis();
        long lastVoice = 0;
        long speechFor = 0;
        try {
            while (true) {
                long now = System.currentTimeMillis();
                if (now - start > maxMs) break;
                int n = r.read(chunk, 0, chunk.length);
                if (n <= 0) continue;
                buf.write(bytesOf(chunk, n));
                if (rms(chunk, n) > SILENCE_RMS) {
                    lastVoice = now;
                    speechFor += (long) (1000.0 * n / RECORD_RATE);
                } else if (lastVoice > 0 && speechFor > MIN_SPEECH_MS
                           && now - lastVoice > SILENCE_MS) {
                    break;     // they stopped talking
                }
            }
        } finally {
            try { r.stop(); } catch (Throwable ignored) {}
            try { r.release(); } catch (Throwable ignored) {}
        }
        out.pcm = buf.toByteArray();
        out.heardSpeech = speechFor > MIN_SPEECH_MS;
        out.ms = System.currentTimeMillis() - start;
        return out;
    }

    /** Little-endian 16-bit mono WAV around raw PCM. */
    static byte[] wrapWav(byte[] pcm, int rate) throws Exception {
        java.io.ByteArrayOutputStream o = new java.io.ByteArrayOutputStream(pcm.length + 44);
        java.io.DataOutputStream d = new java.io.DataOutputStream(o);
        int byteRate = rate * 2;
        d.writeBytes("RIFF");
        d.write(le32(36 + pcm.length));
        d.writeBytes("WAVE");
        d.writeBytes("fmt ");
        d.write(le32(16));
        d.write(le16(1));          // PCM
        d.write(le16(1));          // mono
        d.write(le32(rate));
        d.write(le32(byteRate));
        d.write(le16(2));          // block align
        d.write(le16(16));         // bits
        d.writeBytes("data");
        d.write(le32(pcm.length));
        d.write(pcm);
        d.flush();
        return o.toByteArray();
    }

    private static byte[] le32(int v) {
        return new byte[]{(byte) v, (byte) (v >> 8), (byte) (v >> 16), (byte) (v >> 24)};
    }

    private static byte[] le16(int v) {
        return new byte[]{(byte) v, (byte) (v >> 8)};
    }

    /** PCM lifted out of a RIFF/WAVE container. */
    static final class Pcm {
        byte[] data;
        int rate;

        /**
         * Walk the chunks to find `data`. NOT a fixed 44-byte skip, and the
         * defensiveness has now earned itself twice:
         *
         *   Bulbul answered at 22050 Hz when the docs said the default was
         *   24000 — so the rate is read, never assumed.
         *   xAI answers with a STREAMING header whose RIFF size field is
         *   0x80000023, a placeholder that is negative as a signed int. A
         *   parser that trusted it would compute a wild length; this one
         *   clamps anything negative or past the end to what actually
         *   arrived.
         */
        static Pcm parse(byte[] wav) throws Exception {
            if (wav.length < 44 || wav[0] != 'R' || wav[1] != 'I'
                || wav[2] != 'F' || wav[3] != 'F') {
                throw new IllegalArgumentException("not a RIFF/WAVE payload");
            }
            ByteBuffer b = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN);
            Pcm out = new Pcm();
            out.rate = 24000;
            int pos = 12;
            while (pos + 8 <= wav.length) {
                String id = new String(wav, pos, 4, StandardCharsets.US_ASCII);
                int size = b.getInt(pos + 4);
                int body = pos + 8;
                // SUBTRACT, never add. The obvious form of this check is
                // `body + size > wav.length`, and it is wrong: a streaming
                // WAV writes a placeholder size, and xAI's is large and
                // POSITIVE, so body + size overflows int to a negative
                // number, the check is false, and the clamp never fires.
                // That crashed the app with
                //   OutOfMemoryError: Failed to allocate a 2147483664 byte
                // on the first grok utterance. `size > wav.length - body`
                // asks the same question with operands that cannot overflow.
                if (size < 0 || size > wav.length - body) size = wav.length - body;
                if ("fmt ".equals(id) && size >= 16) {
                    out.rate = b.getInt(body + 4);
                } else if ("data".equals(id)) {
                    out.data = Arrays.copyOfRange(wav, body, body + size);
                    return out;
                }
                pos = body + size + (size % 2);   // chunks are word-aligned
            }
            throw new IllegalArgumentException("no data chunk in the WAV");
        }
    }
}
