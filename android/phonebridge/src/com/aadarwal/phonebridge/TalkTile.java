package com.aadarwal.phonebridge;

import android.content.Intent;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.util.Log;

/**
 * A Quick Settings tile, so Talk is one swipe from anywhere — including the
 * lock screen, which is where a voice assistant is actually wanted.
 *
 * The tile shows state while a turn is in flight, because a voice interface
 * that gives no sign it heard you is the failure this project keeps
 * rediscovering. Nothing is spoken back from here; the controller answers
 * through the bridge once it has something to say.
 */
public class TalkTile extends TileService {

    @Override
    public void onStartListening() {
        setState(Tile.STATE_INACTIVE, "Talk");
    }

    @Override
    public void onClick() {
        setState(Tile.STATE_ACTIVE, "Listening…");
        sendBroadcast(new Intent(TalkReceiver.ACTION)
                          .setPackage(getPackageName()));
        // Recognition is bounded at 20s in TalkReceiver; give the tile back
        // a little after that rather than leaving it stuck on "Listening".
        new android.os.Handler(getMainLooper()).postDelayed(
            () -> setState(Tile.STATE_INACTIVE, "Talk"), 22000);
        Log.i(Listener.TAG, "tile tapped");
    }

    private void setState(int state, String label) {
        Tile t = getQsTile();
        if (t == null) return;          // not bound; nothing to update
        t.setState(state);
        t.setLabel(label);
        t.updateTile();
    }
}
