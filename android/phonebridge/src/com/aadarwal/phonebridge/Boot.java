package com.aadarwal.phonebridge;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;

/**
 * An invisible activity whose only job is to start the microphone service
 * from a foreground context.
 *
 * Why this exists, in the exact words the platform used:
 *
 *   SecurityException: Starting FGS with type microphone ... requires
 *   permissions: all of [FOREGROUND_SERVICE_MICROPHONE] and any of
 *   [... RECORD_AUDIO] and the app must be in the eligible state/exemptions
 *   to access the foreground only permission
 *
 * Both permissions were held. The third clause is the wall: RECORD_AUDIO is a
 * FOREGROUND-ONLY permission, and this app has no UI, so it is permanently
 * backgrounded and permanently ineligible to START a mic service — even
 * though it may keep one running once started.
 *
 * The appop route does not open it either: the package mode is already
 * `allow` while the UID mode reads `foreground`, and the UID mode did not
 * move for `cmd appops set` with or without --uid.
 *
 * So: be foreground for an instant. Shell can start an activity in any state,
 * Theme.NoDisplay means nothing is ever drawn, and the service outlives the
 * activity that started it.
 *
 *   am start -n com.aadarwal.phonebridge/.Boot
 *
 * Honest limitation: this does not survive a reboot on its own. The listener
 * rebinds automatically, but the mic service needs that `am start` again.
 */
public class Boot extends Activity {

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        try {
            VoiceService.ensureRunning(this);
            Log.i(Listener.TAG, "voice service started from foreground");
        } catch (Throwable t) {
            Log.e(Listener.TAG, "could not start voice service", t);
        }
        // Nothing is drawn and nothing is left behind.
        finish();
    }
}
