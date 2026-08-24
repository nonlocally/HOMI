package com.aadarwal.phonebridge;

import android.app.Notification;
import android.app.RemoteInput;
import android.app.PendingIntent;
import android.content.Intent;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Log;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Holds the live notifications, and is the only thing on this device that can
 * act on one.
 *
 * The distinction that justifies this whole app: shell can SEE every
 * notification and every reply action through `dumpsys notification
 * --noredact`. What it gets is text describing a PendingIntent. The
 * PendingIntent itself is a Binder token in system_server, handed only to a
 * bound listener, and there is no way to reconstruct one from its
 * description. So reading is free and acting requires being here.
 */
public class Listener extends NotificationListenerService {

    static final String TAG = "phonebridge";

    /** Bound-ness is not observable from outside, so record it. A bridge that
     *  answers while unbound would return an empty list that looks exactly
     *  like a phone with no notifications. */
    private static volatile boolean connected = false;
    private static volatile Listener instance = null;

    private Bridge bridge;

    @Override
    public void onListenerConnected() {
        connected = true;
        instance = this;
        if (bridge == null) {
            bridge = new Bridge(this);
            bridge.start();
        }
        Log.i(TAG, "listener connected");
    }

    @Override
    public void onListenerDisconnected() {
        connected = false;
        Log.w(TAG, "listener disconnected");
    }

    static boolean isConnected() { return connected; }
    static Listener get() { return instance; }

    // ---------------------------------------------------------------- read

    /**
     * The current notifications, each with whether it can actually be
     * replied to. `repliable` is not a guess: it is the presence of a
     * RemoteInput on one of the notification's own actions.
     */
    List<Map<String, Object>> list() {
        List<Map<String, Object>> out = new ArrayList<>();
        StatusBarNotification[] active;
        try {
            active = getActiveNotifications();
        } catch (SecurityException e) {
            // Bound-ness can lapse. Say so rather than returning [].
            throw new IllegalStateException("listener not bound", e);
        }
        if (active == null) return out;
        for (StatusBarNotification sbn : active) {
            Notification n = sbn.getNotification();
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("key", sbn.getKey());
            row.put("package", sbn.getPackageName());
            row.put("posted", sbn.getPostTime());
            row.put("clearable", sbn.isClearable());
            // A group summary is Android's own rollup, not a message. It has
            // an empty title and duplicates its children, and counting it as
            // a message is how you report two messages for one.
            row.put("group_summary",
                    (n.flags & Notification.FLAG_GROUP_SUMMARY) != 0);

            Bundle ex = n.extras;
            row.put("title", str(ex, Notification.EXTRA_TITLE));
            row.put("text", str(ex, Notification.EXTRA_TEXT));

            List<String> actions = new ArrayList<>();
            boolean repliable = false;
            if (n.actions != null) {
                for (Notification.Action a : n.actions) {
                    if (a == null) continue;
                    actions.add(a.title == null ? "" : a.title.toString());
                    if (a.getRemoteInputs() != null
                            && a.getRemoteInputs().length > 0) {
                        repliable = true;
                    }
                }
            }
            row.put("actions", actions);
            row.put("repliable", repliable);
            out.add(row);
        }
        return out;
    }

    private static String str(Bundle b, String k) {
        if (b == null) return null;
        CharSequence cs = b.getCharSequence(k);
        return cs == null ? null : cs.toString();
    }

    // ---------------------------------------------------------------- act

    /**
     * Type into a notification's reply box and send it.
     *
     * Every RemoteInput on the action gets the same text: an action may
     * declare more than one, and filling only the first leaves the app with a
     * half-populated bundle that it may silently drop.
     */
    void reply(String key, String text) throws Exception {
        StatusBarNotification sbn = find(key);
        Notification.Action action = replyAction(sbn);
        if (action == null) {
            throw new IllegalStateException(
                "that notification has no reply action");
        }
        RemoteInput[] inputs = action.getRemoteInputs();
        Intent fill = new Intent();
        Bundle values = new Bundle();
        for (RemoteInput ri : inputs) {
            values.putCharSequence(ri.getResultKey(), text);
        }
        RemoteInput.addResultsToIntent(inputs, fill, values);
        // Some apps read the text from ClipData instead of the results
        // bundle; addResultsToIntent covers the documented path only.
        action.actionIntent.send(this, 0, fill);
    }

    /** Press a named button on a notification — "Mark as read", "Archive". */
    void action(String key, String title) throws Exception {
        StatusBarNotification sbn = find(key);
        Notification n = sbn.getNotification();
        if (n.actions != null) {
            for (Notification.Action a : n.actions) {
                if (a != null && a.title != null
                        && a.title.toString().equalsIgnoreCase(title)) {
                    if (a.getRemoteInputs() != null
                            && a.getRemoteInputs().length > 0) {
                        // Firing a reply action with no text is not "pressing
                        // a button" — it is sending an empty message.
                        throw new IllegalStateException(
                            "'" + title + "' is a reply box, not a button — "
                            + "use reply");
                    }
                    a.actionIntent.send(this, 0, new Intent());
                    return;
                }
            }
        }
        throw new IllegalStateException("no action titled '" + title + "'");
    }

    void dismiss(String key) {
        cancelNotification(key);
    }

    private StatusBarNotification find(String key) {
        StatusBarNotification[] active = getActiveNotifications();
        if (active != null) {
            for (StatusBarNotification sbn : active) {
                if (sbn.getKey().equals(key)) return sbn;
            }
        }
        // Keys go stale the moment a notification is dismissed or replaced,
        // and a stale key is indistinguishable from a typo. Say which.
        throw new IllegalStateException(
            "no live notification with that key — it may have been dismissed; "
            + "list again");
    }

    private static Notification.Action replyAction(StatusBarNotification sbn) {
        Notification n = sbn.getNotification();
        if (n.actions == null) return null;
        for (Notification.Action a : n.actions) {
            if (a != null && a.getRemoteInputs() != null
                    && a.getRemoteInputs().length > 0) {
                return a;
            }
        }
        return null;
    }
}
