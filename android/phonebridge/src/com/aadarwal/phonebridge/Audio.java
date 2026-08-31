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
