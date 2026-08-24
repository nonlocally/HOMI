package com.aadarwal.phonebridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;
import android.util.Log;

/**
 * A microphone foreground service, and the reason it exists is measured, not
 * assumed.
 *
 * With no foreground service, `listen` returned an EMPTY callback trace after
 * 12 seconds: not "ready", not "rms", not even an error. The recogniser never
 * started at all. Android has blocked background microphone access since 9,
 * and from 14 a service that touches the mic must declare
 * foregroundServiceType="microphone" and hold FOREGROUND_SERVICE_MICROPHONE.
 *
 * That the documentation left this ambiguous — SpeechRecognizer delegates
 * capture to a separate system RecognitionService process, so it was not
 * obvious whose foreground state counts — is exactly why the trace was worth
 * adding before guessing. An empty trace and a trace ending in
 * "no speech heard" mean completely different things and are the same string
 * without it.
 *
 * The notification is not overhead: Android requires one, and it is the only
 * honest signal to the phone's owner that something can hear them.
 */
public class VoiceService extends Service {

    private static final String CHANNEL = "phonebridge";
    private static final int NOTIF_ID = 1;

    static void ensureRunning(Context ctx) {
        try {
            ctx.startForegroundService(new Intent(ctx, VoiceService.class));
        } catch (Throwable t) {
            // Starting a mic FGS from the background is itself restricted.
            // Being called from onListenerConnected — a system-initiated bind
            // — is what makes this permissible; if that ever stops being
            // true, this is where it will show up.
            Log.e(Listener.TAG, "could not start the voice service", t);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(
            CHANNEL, "phonebridge", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Lets the bridge use the microphone");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);

        // The Talk button IS the interface. Everything else this app does is
        // invisible by design; this is the one part a person touches.
        android.app.PendingIntent talk = android.app.PendingIntent.getBroadcast(
            this, 0,
            new Intent(TalkReceiver.ACTION).setPackage(getPackageName()),
            android.app.PendingIntent.FLAG_IMMUTABLE
                | android.app.PendingIntent.FLAG_UPDATE_CURRENT);

        Notification n = new Notification.Builder(this, CHANNEL)
            .setContentTitle("phonebridge")
            .setContentText("tap Talk to speak")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .addAction(new Notification.Action.Builder(
                android.graphics.drawable.Icon.createWithResource(
                    this, android.R.drawable.ic_btn_speak_now),
                "Talk", talk).build())
            .build();

        startForeground(NOTIF_ID, n,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        Log.i(Listener.TAG, "voice service foregrounded");
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Sticky: the mic must be available whenever the bridge is asked, not
        // only just after a restart.
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
