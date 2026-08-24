package com.aadarwal.phonebridge;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.List;
import java.util.Map;

/**
 * A line-of-JSON-in, line-of-JSON-out server on loopback.
 *
 * Same shape as `phone shelld`, and reached the same way — an ssh -L forward
 * from the controller — so nothing new is exposed off-device and the idiom is
 * one the rest of this project already uses.
 *
 * Loopback is not by itself a permission boundary: every app on the phone can
 * open a socket to 127.0.0.1. Hence the token, which is written where the
 * device shell can read it and other apps cannot.
 */
class Bridge implements Runnable {

    /** Fixed so the ssh forward can be written down once. */
    static final int PORT = 8127;

    private final Context ctx;
    private final String token;
    private final Voice voice;
    private final Speak speak;
    private Thread thread;

    Bridge(Context ctx) {
        this.ctx = ctx;
        this.token = loadOrMintToken(ctx);
        this.voice = new Voice(ctx);
        // Built at construction, not on first use: the whole point is that
        // the engine is already bound when someone asks it to talk.
        this.speak = new Speak(ctx);
    }

    void start() {
        if (thread != null) return;
        thread = new Thread(this, "phonebridge");
        thread.setDaemon(true);
        thread.start();
    }

    /**
     * A random token, persisted, in the app's own external files directory —
     * which the device shell can read and other apps cannot. That asymmetry
     * is the whole access-control story: whoever already has shell on this
     * phone can drive the bridge, and nobody else can.
     */
    private static String loadOrMintToken(Context ctx) {
        File dir = ctx.getExternalFilesDir(null);
        if (dir == null) dir = ctx.getFilesDir();
        File f = new File(dir, "token");
        try {
            if (f.exists()) {
                byte[] b = new byte[64];
                int n = new java.io.FileInputStream(f).read(b);
                if (n > 0) return new String(b, 0, n, StandardCharsets.UTF_8).trim();
            }
        } catch (Exception e) {
            Log.w(Listener.TAG, "token read failed, minting a new one", e);
        }
        byte[] raw = new byte[24];
        new SecureRandom().nextBytes(raw);
        StringBuilder sb = new StringBuilder();
        for (byte x : raw) sb.append(String.format("%02x", x));
        String t = sb.toString();
        try (FileOutputStream os = new FileOutputStream(f)) {
            os.write(t.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            Log.e(Listener.TAG, "could not persist token", e);
        }
        return t;
    }

    @Override
    public void run() {
        ServerSocket server = null;
        try {
            server = new ServerSocket(PORT, 8, InetAddress.getByName("127.0.0.1"));
            Log.i(Listener.TAG, "bridge listening on 127.0.0.1:" + PORT);
            while (!Thread.currentThread().isInterrupted()) {
                Socket s = server.accept();
                handle(s);
            }
        } catch (Exception e) {
            Log.e(Listener.TAG, "bridge stopped", e);
        } finally {
            try { if (server != null) server.close(); } catch (Exception ignored) {}
        }
    }

    private void handle(Socket s) {
        try {
            s.setSoTimeout(15000);
            BufferedReader in = new BufferedReader(
                new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            String line = in.readLine();
            JSONObject reply = dispatch(line);
            OutputStream out = s.getOutputStream();
            out.write((reply.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
        } catch (Exception e) {
            Log.w(Listener.TAG, "request failed", e);
        } finally {
            try { s.close(); } catch (Exception ignored) {}
        }
    }

    private JSONObject dispatch(String line) {
        JSONObject r = new JSONObject();
        try {
            if (line == null || line.isEmpty()) return err(r, "empty request");
            JSONObject q = new JSONObject(line);
            if (!constantTimeEquals(token, q.optString("token", ""))) {
                return err(r, "bad token");
            }
            // Bound-ness is not observable from the caller's side. An unbound
            // listener returns an empty list, which looks exactly like a
            // phone with nothing on it — so refuse instead of answering.
            String op = q.optString("op", "");
            // Voice does not go through the notification listener at all, so
            // gating it on bound-ness would refuse a working capability for
            // an unrelated reason — a wrong diagnosis, which this project
            // treats as its own class of bug.
            boolean needsListener = !("ping".equals(op)
                    || "voice_status".equals(op)
                    || "voice_download".equals(op)
                    || "listen".equals(op)
                    || "say".equals(op)
                    || "say_status".equals(op)
                    || "shut_up".equals(op)
                    || "await_turn".equals(op));
            if (needsListener && (!Listener.isConnected() || Listener.get() == null)) {
                return err(r, "notification listener is not bound — "
                              + "cmd notification allow_listener, then reboot "
                              + "or toggle it");
            }
            Listener L = Listener.get();
            switch (op) {
                case "ping":
                    r.put("ok", true);
                    r.put("bound", true);
                    return r;
                case "voice_status":
                    for (Map.Entry<String, Object> e : voice.status().entrySet()) {
                        Object v = e.getValue();
                        r.put(e.getKey(), v instanceof List
                              ? new JSONArray((List<?>) v)
                              : (v == null ? JSONObject.NULL : v));
                    }
                    r.put("ok", true);
                    return r;
                case "voice_download": {
                    for (Map.Entry<String, Object> e :
                            voice.download(q.optInt("wait", 60)).entrySet()) {
                        r.put(e.getKey(), e.getValue());
                    }
                    return r;
                }
                case "await_turn": {
                    // LONG POLL. The controller blocks here until someone
                    // taps Talk and speaks, so the microphone opens only for
                    // the length of a sentence instead of continuously — the
                    // difference between a phone that lasts the day and one
                    // that went 50% to 20% in two hours.
                    String heard = Turns.take(q.optInt("timeout", 300));
                    if (heard == null) {
                        r.put("ok", false);
                        r.put("err", "no turn within the wait");
                        r.put("timeout", true);   // NOT a failure; poll again
                    } else {
                        r.put("ok", true);
                        r.put("text", heard);
                    }
                    return r;
                }
                case "listen": {
                    for (Map.Entry<String, Object> e :
                            voice.listen(q.optInt("timeout", 20)).entrySet()) {
                        Object v = e.getValue();
                        r.put(e.getKey(), v instanceof List
                              ? new JSONArray((List<?>) v)
                              : (v == null ? JSONObject.NULL : v));
                    }
                    return r;
                }
                case "say": {
                    for (Map.Entry<String, Object> e :
                            speak.say(q.optString("text", ""),
                                      q.optInt("timeout", 60)).entrySet()) {
                        r.put(e.getKey(), e.getValue());
                    }
                    return r;
                }
                case "say_status": {
                    for (Map.Entry<String, Object> e : speak.status().entrySet()) {
                        r.put(e.getKey(), e.getValue());
                    }
                    r.put("ok", true);
                    return r;
                }
                case "shut_up":
                    speak.stop();
                    r.put("ok", true);
                    return r;
                case "list": {
                    JSONArray arr = new JSONArray();
                    for (Map<String, Object> row : L.list()) {
                        JSONObject o = new JSONObject();
                        for (Map.Entry<String, Object> e : row.entrySet()) {
                            Object v = e.getValue();
                            if (v instanceof List) {
                                o.put(e.getKey(), new JSONArray((List<?>) v));
                            } else {
                                o.put(e.getKey(), v == null ? JSONObject.NULL : v);
                            }
                        }
                        arr.put(o);
                    }
                    r.put("ok", true);
                    r.put("notifications", arr);
                    return r;
                }
                case "reply": {
                    String key = q.optString("key", "");
                    String text = q.optString("text", "");
                    if (key.isEmpty()) return err(r, "reply needs a key");
                    // An empty reply is a message. Refuse it rather than
                    // sending nothing to somebody.
                    if (text.trim().isEmpty()) {
                        return err(r, "refusing to send an empty message");
                    }
                    L.reply(key, text);
                    r.put("ok", true);
                    r.put("sent", text);
                    return r;
                }
                case "action": {
                    L.action(q.optString("key", ""), q.optString("title", ""));
                    r.put("ok", true);
                    return r;
                }
                case "dismiss": {
                    L.dismiss(q.optString("key", ""));
                    r.put("ok", true);
                    return r;
                }
                default:
                    return err(r, "unknown op '" + op + "'");
            }
        } catch (Exception e) {
            String m = e.getMessage();
            return err(r, m == null ? e.getClass().getSimpleName() : m);
        }
    }

    private static JSONObject err(JSONObject r, String msg) {
        try {
            r.put("ok", false);
            r.put("err", msg);
        } catch (Exception ignored) {}
        return r;
    }

    /** Length-independent compare, so a wrong token cannot be found byte by
     *  byte from the outside. */
    private static boolean constantTimeEquals(String a, String b) {
        if (a == null || b == null) return false;
        byte[] x = a.getBytes(StandardCharsets.UTF_8);
        byte[] y = b.getBytes(StandardCharsets.UTF_8);
        int diff = x.length ^ y.length;
        for (int i = 0; i < x.length && i < y.length; i++) diff |= x[i] ^ y[i];
        return diff == 0;
    }
}
