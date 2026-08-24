package com.aadarwal.phonebridge;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * The board, over HTTP, from the phone.
 *
 * The app renders its own home screen but does NOT own any of this data. The
 * roster, the conversations and the send path all live in the fabric, and the
 * phone asks for them exactly like the browser does. That is the line: a
 * native UI is a second VIEW, and a second view is fine; a second store would
 * be a second truth, and this is the device most likely to be lost or flat.
 *
 * On the token. The board mints a fresh one per serve and injects it into the
 * pages it serves — it is explicitly not an auth factor (access control is the
 * tailnet plus the Host pin); it is CSRF defence and a session binding, so a
 * stale client's writes fail instead of acting. Which means the honest way for
 * this app to get one is the same way the page does: ask for a served page and
 * read it out. A token planted at install time would go stale on the next
 * board restart and fail silently, which is the failure mode this whole
 * evening was made of.
 */
final class Api {

    /** Same origin the WebView loads. Reachable from the phone over Tailscale. */
    static final String BASE = "https://agents.aadarwal.com";

    /** Any valid target works — we want the page's boot blob, not its content. */
    private static final String BOOT_SEED = "fable";

    private static volatile String token = null;
    private static volatile String handle = null;
    private static volatile String device = null;

    private Api() {}

    /**
     * Learn which device this is, from the file the installer plants.
     *
     * An app cannot see the tailnet, so it cannot discover its own fabric
     * name — but the router needs it, or a question like "what is my battery"
     * has no phone to ask and escalates to an agent instead. Measured before
     * this existed: 48 seconds and an escalation, for a question the regex
     * tier answers in about one.
     */
    static void init(android.content.Context ctx) {
        if (device != null) return;
        try {
            java.io.File dir = ctx.getExternalFilesDir(null);
            if (dir == null) return;
            java.io.File f = new java.io.File(dir, "device");
            if (!f.exists()) return;
            byte[] b = new byte[128];
            int n = new java.io.FileInputStream(f).read(b);
            if (n > 0) device = new String(b, 0, n, StandardCharsets.UTF_8).trim();
        } catch (Exception e) {
            Log.w(Listener.TAG, "could not read the device name", e);
        }
    }

    static String handle() {
        return handle;
    }

    // ---------------------------------------------------------------- wire

    private static String get(String path, boolean withToken) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(BASE + path).openConnection();
        c.setRequestMethod("GET");
        c.setConnectTimeout(10000);
        c.setReadTimeout(20000);
        c.setRequestProperty("Cache-Control", "no-store");
        if (withToken && token != null) c.setRequestProperty("X-Homi-Token", token);
        try {
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            String body = slurp(in);
            if (code >= 400) throw new ApiError(code, body);
            return body;
        } finally {
            c.disconnect();
        }
    }

    private static String slurp(InputStream in) throws Exception {
        if (in == null) return "";
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        // Bounded: a body this app cannot use is a body it should not hold.
        while ((n = in.read(buf)) > 0 && out.size() < 4 * 1024 * 1024) {
            out.write(buf, 0, n);
        }
        return out.toString("UTF-8");
    }

    /** Thrown with the server's own words, so a failure can be shown rather
     *  than turned into a generic "something went wrong". */
    static final class ApiError extends Exception {
        final int code;
        ApiError(int code, String body) {
            super("HTTP " + code + (body == null || body.isEmpty()
                                    ? "" : ": " + body.trim()));
            this.code = code;
        }
    }

    // --------------------------------------------------------------- token

    /**
     * Fetch (or re-fetch) the mutation token by reading a served page.
     * Cheap, and correct across board restarts — which happen, and which
     * invalidate every previously held token.
     */
    static synchronized void refreshToken() throws Exception {
        String page = get("/talk/" + BOOT_SEED, false);
        int i = page.indexOf("id=\"talk-boot\"");
        if (i < 0) throw new Exception("no boot blob in /talk — is a handle claimed?");
        int a = page.indexOf('>', i);
        int b = page.indexOf("</script>", a);
        if (a < 0 || b < 0) throw new Exception("malformed boot blob");
        JSONObject boot = new JSONObject(page.substring(a + 1, b));
        token = boot.optString("token", null);
        handle = boot.optString("handle", null);
        if (token == null || token.isEmpty()) throw new Exception("no token in boot blob");
    }

    private static void ensureToken() throws Exception {
        if (token == null) refreshToken();
    }

    // --------------------------------------------------------------- reads

    /** One agent as the home screen needs it. */
    static final class Agent {
        String name = "";
        String device = "";
        String kind = "";
        String state = "";
        int undelivered = 0;
        boolean live() { return "live".equals(state); }
    }

    /**
     * The roster, flattened out of the per-device shape the board publishes.
     *
     * Sorted live-first then by unread, because the list is read top-down by
     * someone deciding who to talk to — not browsed. A device grouping would
     * be truer to the fabric's structure and worse for that decision.
     */
    static List<Agent> roster() throws Exception {
        JSONObject st = new JSONObject(get("/state.json", false));
        JSONArray devs = st.optJSONArray("devices");
        List<Agent> out = new ArrayList<>();
        if (devs == null) return out;
        for (int i = 0; i < devs.length(); i++) {
            JSONObject d = devs.optJSONObject(i);
            if (d == null) continue;
            String dev = d.optString("device", "");
            JSONArray ags = d.optJSONArray("agents");
            if (ags == null) continue;
            for (int j = 0; j < ags.length(); j++) {
                JSONObject a = ags.optJSONObject(j);
                if (a == null) continue;
                Agent ag = new Agent();
                ag.name = a.optString("name", "");
                if (ag.name.isEmpty()) continue;
                ag.device = dev;
                ag.kind = a.optString("kind", "");
                ag.state = a.optString("state", "");
                ag.undelivered = a.optInt("undelivered", 0);
                out.add(ag);
            }
        }
        java.util.Collections.sort(out, (x, y) -> {
            if (x.live() != y.live()) return x.live() ? -1 : 1;
            if (x.undelivered != y.undelivered) return y.undelivered - x.undelivered;
            return x.name.compareToIgnoreCase(y.name);
        });
        return out;
    }

    /** One line of a conversation. */
    static final class Entry {
        double ts;
        String dir = "";
        String text = "";
        boolean inbound() { return "in".equals(dir); }
    }

    static List<Entry> conversation(String target, double since) throws Exception {
        ensureToken();
        String path = "/api/conv/" + enc(target) + "?since=" + since;
        String body;
        try {
            body = get(path, true);
        } catch (ApiError e) {
            // A restarted board mints a new token; the old one 403s. Re-read
            // it and try once more rather than showing the person a failure
            // they can do nothing about.
            if (e.code != 403) throw e;
            refreshToken();
            body = get(path, true);
        }
        JSONObject o = new JSONObject(body);
        JSONArray arr = o.optJSONArray("entries");
        List<Entry> out = new ArrayList<>();
        if (arr == null) return out;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject e = arr.optJSONObject(i);
            if (e == null) continue;
            Entry x = new Entry();
            x.ts = e.optDouble("ts", 0);
            x.dir = e.optString("dir", "");
            x.text = e.optString("text", "");
            out.add(x);
        }
        return out;
    }

    // --------------------------------------------------------------- write

    /**
     * Send. The header set is not decoration: the board requires a JSON
     * content type AND the X-Homi header precisely because no cross-origin
     * form can send either without a preflight it cannot satisfy.
     *
     * `voice` marks the turn as spoken IN THE TEXT, so the agent knows it is
     * being listened to rather than read, and so the human's own transcript
     * shows exactly what the agent was handed.
     */
    static void send(String target, String text, boolean voice) throws Exception {
        ensureToken();
        try {
            post(target, text, voice);
        } catch (ApiError e) {
            if (e.code != 403) throw e;
            refreshToken();
            post(target, text, voice);
        }
    }

    private static void post(String target, String text, boolean voice) throws Exception {
        JSONObject req = new JSONObject();
        req.put("to", target);
        req.put("text", text);
        req.put("voice", voice);
        byte[] body = req.toString().getBytes(StandardCharsets.UTF_8);

        HttpURLConnection c = (HttpURLConnection) new URL(BASE + "/api/send").openConnection();
        try {
            c.setRequestMethod("POST");
            c.setConnectTimeout(10000);
            c.setReadTimeout(30000);
            c.setDoOutput(true);
            c.setFixedLengthStreamingMode(body.length);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("X-Homi", "1");
            c.setRequestProperty("X-Homi-Token", token);
            OutputStream os = c.getOutputStream();
            os.write(body);
            os.flush();
            int code = c.getResponseCode();
            String resp = slurp(code >= 400 ? c.getErrorStream() : c.getInputStream());
            if (code >= 400) throw new ApiError(code, resp);
            JSONObject o = new JSONObject(resp.isEmpty() ? "{}" : resp);
            if (o.has("ok") && !o.optBoolean("ok")) {
                throw new Exception(o.optString("err", "send refused"));
            }
        } finally {
            c.disconnect();
        }
    }

    /** What the ladder answered, and which tier answered it. */
    static final class Answer {
        String tier = "";
        String text = "";
        boolean escalate = false;
    }

    /**
     * Put a question to the tier ladder: regex, then the small fast model,
     * then — only if those cannot — a report that it needs an agent.
     *
     * This is the path the TALK button was missing. Without it every spoken
     * turn went straight to whichever agent was selected, so "tell me about
     * X" reached a frontier coding model at high effort and took the best
     * part of a minute. The middle tier answers the same question in four
     * seconds from its own knowledge.
     */
    static Answer ask(String text) throws Exception {
        ensureToken();
        JSONObject req = new JSONObject();
        req.put("text", text);
        if (device != null && !device.isEmpty()) req.put("device", device);
        byte[] body = req.toString().getBytes(StandardCharsets.UTF_8);

        HttpURLConnection c = (HttpURLConnection) new URL(BASE + "/api/ask").openConnection();
        try {
            c.setRequestMethod("POST");
            c.setConnectTimeout(10000);
            // The router is allowed to think. A read timeout shorter than the
            // ladder's own budget would turn a slow answer into a failure and
            // send the turn to an agent — the exact escalation this avoids.
            c.setReadTimeout(120000);
            c.setDoOutput(true);
            c.setFixedLengthStreamingMode(body.length);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("X-Homi", "1");
            c.setRequestProperty("X-Homi-Token", token);
            OutputStream os = c.getOutputStream();
            os.write(body);
            os.flush();
            int code = c.getResponseCode();
            String resp = slurp(code >= 400 ? c.getErrorStream() : c.getInputStream());
            if (code == 403) {
                refreshToken();
                return ask(text);
            }
            if (code >= 400) throw new ApiError(code, resp);
            JSONObject o = new JSONObject(resp.isEmpty() ? "{}" : resp);
            Answer a = new Answer();
            a.tier = o.optString("tier", "");
            a.text = o.optString("answer", "");
            a.escalate = o.optBoolean("escalate", false);
            return a;
        } finally {
            c.disconnect();
        }
    }

    /** The in-app transcript URL for one agent. */
    static String talkUrl(String target) {
        return BASE + "/talk/" + enc(target);
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (Exception e) {
            Log.w(Listener.TAG, "encode failed", e);
            return s;
        }
    }
}
