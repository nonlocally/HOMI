package com.aadarwal.phonebridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * What the Talk button on the notification actually does.
 *
 * A BroadcastReceiver rather than an Activity because tapping Talk should not
 * bring anything to the screen — the whole point is to speak to the phone
 * without looking at it, from the lock screen if need be.
 *
 * Recognition runs on a background thread here; Voice hops to the main
 * looper itself, which SpeechRecognizer requires and enforces at runtime.
 */
public class TalkReceiver extends BroadcastReceiver {

    static final String ACTION = "com.aadarwal.phonebridge.TALK";

    @Override
    public void onReceive(Context ctx, Intent intent) {
        // goAsync() so the process is not killed the moment onReceive
        // returns — recognition takes seconds, and a receiver is normally
        // given about ten before the system reclaims it.
        final PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                Voice v = new Voice(ctx.getApplicationContext());
                java.util.Map<String, Object> got = v.listen(20);
                Object text = got.get("text");
                if (Boolean.TRUE.equals(got.get("ok")) && text != null) {
                    Log.i(Listener.TAG, "talk: heard " + text.toString().length()
                                        + " chars");
                    Turns.offer(text.toString());
                } else {
                    // Nothing heard is not an error worth surfacing on the
                    // phone — the person will simply tap again. Logged, not
                    // announced.
                    Log.i(Listener.TAG, "talk: nothing heard ("
                                        + got.get("err") + ")");
                }
            } catch (Throwable t) {
                Log.e(Listener.TAG, "talk failed", t);
            } finally {
                pending.finish();
            }
        }, "talk-tap").start();
    }
}
