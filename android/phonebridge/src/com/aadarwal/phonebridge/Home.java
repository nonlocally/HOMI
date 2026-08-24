package com.aadarwal.phonebridge;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * homi's home screen — native, and the thing that was missing.
 *
 * The app already had every capability a browser cannot have: notification
 * read and reply, on-device recognition, a warm speech engine. What it did not
 * have was a FACE. Opening it dropped you on the same web page you could reach
 * from Chrome, so nothing about it read as an app, and none of the native
 * plumbing was visibly doing anything. That was a design mistake of mine and
 * this screen is the correction.
 *
 * The line it holds: this renders, it does not store. The roster comes from
 * /state.json, the conversation from /api/conv, the send goes to /api/send.
 * Reusing the fabric's data was always right; showing an unchanged website was
 * not. A second view is fine. A second source of truth is not.
 *
 * One tap does one thing:
 *   TALK          speak, send, wait for the answer, say it out loud
 *   the target    a chooser, so TALK's destination is never a guess
 *   an agent row  opens that conversation's transcript, in this app
 *   a reply       sends into the notification it came from
 */
public class Home extends Activity {

    private static final String PREFS  = "homi";
    private static final String K_TARGET = "talk_target";
    private static final String DEFAULT_TARGET = "fable";

    /** How long to wait for an agent to answer a spoken turn before saying so. */
    private static final int ANSWER_WAIT_S = 150;

    private final Handler ui = new Handler(Looper.getMainLooper());
    // Single thread on purpose: the voice engine, the send and the poll are
    // one conversation, and running them concurrently would let a second TALK
    // interleave with the first one's answer.
    private final ExecutorService work = Executors.newSingleThreadExecutor();

    private Voice voice;
    private Speak speak;

    private TextView  talkLabel;
    private TextView  talkSub;
    private View      talkCard;
    private TextView  statusLine;
    private LinearLayout agentList;
    private LinearLayout notifList;
    private TextView  notifCount;
    private TextView  headerNote;

    private volatile boolean busy = false;
    private volatile boolean visible = false;
    private String target = DEFAULT_TARGET;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        voice = new Voice(this);
        speak = new Speak(this);
        SharedPreferences p = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        target = p.getString(K_TARGET, DEFAULT_TARGET);
        Api.init(this);          // which device this is, for phone questions

        getWindow().setStatusBarColor(Ui.BG);
        getWindow().setNavigationBarColor(Ui.BG);
        setContentView(buildScreen());

        // The mic service belongs to the app, not to this window — opening
        // homi should not be what makes voice work, and closing it should not
        // break the Talk notification.
        VoiceService.ensureRunning(this);
    }

    // ------------------------------------------------------------ the screen

    private View buildScreen() {
        int pad = Ui.dp(this, 16);

        LinearLayout col = Ui.column(this);
        col.setPadding(pad, 0, pad, 0);
        col.setBackgroundColor(Ui.BG);
        // The status bar and the gesture bar are the platform's, not ours.
        Ui.fitSystemBars(col, Ui.dp(this, 14), Ui.dp(this, 24));

        // header ------------------------------------------------------------
        LinearLayout head = Ui.row(this);
        TextView title = Ui.text(this, "homi", 28, Ui.TEXT);
        title.setLetterSpacing(-0.02f);
        head.addView(title, Ui.lpWrap());
        headerNote = Ui.text(this, "", 12, Ui.DIM);
        headerNote.setGravity(Gravity.END);
        head.addView(headerNote, Ui.lpGrow());
        col.addView(head, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));

        // the talk card ------------------------------------------------------
        LinearLayout talk = Ui.column(this);
        talk.setGravity(Gravity.CENTER);
        talk.setBackground(Ui.round(Ui.ACCENT, Ui.dp(this, 20)));
        talk.setPadding(pad, Ui.dp(this, 26), pad, Ui.dp(this, 26));
        talkLabel = Ui.text(this, "TALK", 30, Color(0xFF0d0f12));
        talkLabel.setLetterSpacing(0.18f);
        talkLabel.setGravity(Gravity.CENTER);
        talk.addView(talkLabel, Ui.lpWrap());
        talkSub = Ui.text(this, "", 13, Color(0xCC0d0f12));
        talkSub.setGravity(Gravity.CENTER);
        talk.addView(talkSub, Ui.lpWrap());
        talk.setOnClickListener(v -> onTalk());
        talkCard = talk;
        LinearLayout.LayoutParams tp = Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT);
        tp.setMargins(0, Ui.dp(this, 20), 0, Ui.dp(this, 10));
        col.addView(talk, tp);

        // who TALK goes to, always visible so it is never a guess -------------
        LinearLayout pick = Ui.row(this);
        pick.setPadding(Ui.dp(this, 4), Ui.dp(this, 6), Ui.dp(this, 4), Ui.dp(this, 6));
        TextView pickLabel = Ui.text(this, "", 13, Ui.DIM);
        pick.addView(pickLabel, Ui.lpGrow());
        TextView chev = Ui.text(this, "change", 13, Ui.ACCENT);
        pick.addView(chev, Ui.lpWrap());
        pick.setOnClickListener(v -> chooseTarget());
        col.addView(pick, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));
        pickTargetLabel = pickLabel;

        // a status line that says what just happened -------------------------
        statusLine = Ui.text(this, "", 13, Ui.DIM);
        statusLine.setPadding(Ui.dp(this, 4), Ui.dp(this, 4), Ui.dp(this, 4), 0);
        col.addView(statusLine, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));

        // agents --------------------------------------------------------------
        col.addView(Ui.heading(this, "agents"),
                    Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));
        agentList = Ui.column(this);
        col.addView(agentList, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));

        // notifications --------------------------------------------------------
        LinearLayout nhead = Ui.row(this);
        nhead.addView(Ui.heading(this, "notifications"), Ui.lpWrap());
        notifCount = Ui.text(this, "", 11, Ui.DIM);
        notifCount.setGravity(Gravity.END);
        notifCount.setPadding(0, Ui.dp(this, 18), Ui.dp(this, 4), Ui.dp(this, 8));
        nhead.addView(notifCount, Ui.lpGrow());
        col.addView(nhead, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));
        notifList = Ui.column(this);
        col.addView(notifList, Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Ui.BG);
        sv.setFillViewport(true);
        sv.addView(col, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT));
        return sv;
    }

    private TextView pickTargetLabel;

    private static int Color(long argb) {
        return (int) argb;
    }

    @Override
    protected void onResume() {
        super.onResume();
        visible = true;
        showTarget();
        refresh();
    }

    @Override
    protected void onPause() {
        visible = false;
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        work.shutdownNow();
        super.onDestroy();
    }

    // ------------------------------------------------------------- the roster

    private void refresh() {
        renderNotifications();          // in-process, instant, no network
        // Say we are fetching. An empty list while the request is in flight
        // and an empty list because there are no agents are the same picture,
        // and the first screenshot of this screen showed exactly that: a bare
        // "AGENTS" heading with nothing under it, which reads as broken.
        if (agentList.getChildCount() == 0) {
            agentList.addView(note("loading the roster…", Ui.DIM));
        }
        work.execute(() -> {
            try {
                List<Api.Agent> roster = Api.roster();
                ui.post(() -> {
                    renderAgents(roster);
                    String h = Api.handle();
                    headerNote.setText(h == null ? "" : "you are " + h);
                });
            } catch (Exception e) {
                Log.w(Listener.TAG, "roster failed", e);
                // Name the failure. An empty list that means "the board is
                // unreachable" and an empty list that means "no agents" are
                // the same picture and completely different problems.
                ui.post(() -> {
                    agentList.removeAllViews();
                    agentList.addView(note("could not reach the board — "
                                           + short_(e), Ui.ERR));
                });
            }
        });
    }

    private void renderAgents(List<Api.Agent> roster) {
        agentList.removeAllViews();
        if (roster.isEmpty()) {
            agentList.addView(note("no agents on the fabric", Ui.DIM));
            return;
        }
        for (Api.Agent a : roster) {
            agentList.addView(agentRow(a), Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));
        }
    }

    private View agentRow(final Api.Agent a) {
        LinearLayout r = Ui.row(this);
        r.setBackground(Ui.round(a.name.equals(target) ? Ui.CARD_HI : Ui.CARD,
                                 Ui.dp(this, 14), Ui.dp(this, 1),
                                 a.name.equals(target) ? Ui.ACCENT : Ui.LINE));
        int p = Ui.dp(this, 14);
        r.setPadding(p, p, p, p);

        View d = new View(this);
        d.setBackground(Ui.dot(a.live() ? Ui.LIVE : Ui.IDLE));
        LinearLayout.LayoutParams dp = Ui.lp(Ui.dp(this, 8), Ui.dp(this, 8));
        dp.setMargins(0, 0, Ui.dp(this, 12), 0);
        r.addView(d, dp);

        LinearLayout mid = Ui.column(this);
        mid.addView(Ui.text(this, a.name, 17, Ui.TEXT), Ui.lpWrap());
        String sub = a.device + (a.kind.isEmpty() ? "" : " · " + a.kind);
        mid.addView(Ui.text(this, sub, 12, Ui.DIM), Ui.lpWrap());
        r.addView(mid, Ui.lpGrow());

        if (a.undelivered > 0) {
            TextView b = Ui.text(this, String.valueOf(a.undelivered), 12, Ui.BG);
            b.setBackground(Ui.round(Ui.WARN, Ui.dp(this, 10)));
            b.setPadding(Ui.dp(this, 8), Ui.dp(this, 3), Ui.dp(this, 8), Ui.dp(this, 3));
            r.addView(b, Ui.lpWrap());
        }

        LinearLayout.LayoutParams lp = Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, Ui.dp(this, 8));
        r.setLayoutParams(lp);
        r.setOnClickListener(v -> openTranscript(a.name));
        // Long-press retargets without leaving the screen — the same thing
        // "change" does, for someone whose thumb is already on the row.
        r.setOnLongClickListener(v -> { setTarget(a.name); return true; });
        return r;
    }

    private void openTranscript(String name) {
        Intent i = new Intent(this, Homi.class);
        i.putExtra(Homi.EXTRA_URL, Api.talkUrl(name));
        i.putExtra(Homi.EXTRA_TITLE, name);
        startActivity(i);
    }

    // ------------------------------------------------------------ the target

    private void showTarget() {
        pickTargetLabel.setText("TALK goes to " + target);
        talkSub.setText(busy ? "" : "→ " + target);
    }

    private void setTarget(String name) {
        target = name;
        getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(K_TARGET, name).apply();
        showTarget();
        refresh();                       // re-render so the selection shows
    }

    private void chooseTarget() {
        work.execute(() -> {
            List<Api.Agent> roster;
            try {
                roster = Api.roster();
            } catch (Exception e) {
                ui.post(() -> status("could not load the roster — " + short_(e), Ui.ERR));
                return;
            }
            final List<String> names = new ArrayList<>();
            for (Api.Agent a : roster) {
                names.add(a.name + (a.live() ? "  ·  live" : ""));
            }
            final List<String> plain = new ArrayList<>();
            for (Api.Agent a : roster) plain.add(a.name);
            ui.post(() -> {
                if (plain.isEmpty()) {
                    status("no agents to talk to", Ui.DIM);
                    return;
                }
                new AlertDialog.Builder(this)
                    .setTitle("talk to")
                    .setItems(names.toArray(new String[0]),
                              (d, which) -> setTarget(plain.get(which)))
                    .show();
            });
        });
    }

    // -------------------------------------------------------------- the turn

    /**
     * The whole spoken turn, in order, on one thread: listen, send, wait,
     * speak. Each step reports what it did — a turn that fails silently is
     * indistinguishable from one nobody heard, and that ambiguity is what
     * makes voice interfaces feel broken.
     */
    private void onTalk() {
        if (busy) {
            status("still working on the last one", Ui.DIM);
            return;
        }
        busy = true;
        setTalkState("LISTENING", "speak now");
        work.execute(() -> {
            String heard = "";
            try {
                Map<String, Object> got = voice.listen(20);
                Object t = got.get("text");
                if (Boolean.TRUE.equals(got.get("ok")) && t != null) {
                    heard = t.toString().trim();
                }
                if (heard.isEmpty()) {
                    Object err = got.get("error");
                    finish_(err == null ? "didn't catch that"
                                        : "didn't catch that — " + err, Ui.WARN);
                    return;
                }
            } catch (Throwable e) {
                Log.e(Listener.TAG, "listen failed", e);
                finish_("the microphone failed — " + short_(e), Ui.ERR);
                return;
            }

            final String said = heard;
            ui.post(() -> {
                status("you: " + said, Ui.TEXT);
                setTalkState("THINKING", "asking the router");
            });

            // THE LADDER FIRST, and this is the whole point of the button.
            //
            // Sending straight to the selected agent — which is what this did
            // at first — put "tell me about <person>" in front of a frontier
            // coding model at high reasoning effort: forty seconds, and a
            // price to match, for something a small fast model answers from
            // its own knowledge in four. The agent tier is for WORK. It is
            // reached when the router says so, and the tier that answered is
            // shown on screen so it is never a mystery which one ran.
            Api.Answer routed = null;
            try {
                routed = Api.ask(said);
            } catch (Exception e) {
                Log.w(Listener.TAG, "router failed", e);
                // A router that could not run is not a decision to escalate.
                // Say so, and let the person decide whether to spend an agent
                // on it — silently promoting every failure to the expensive
                // tier is exactly the surprise this is fixing.
                finish_("the router did not answer — " + short_(e)
                        + " (tap again, or open " + target + " to ask directly)",
                        Ui.ERR);
                return;
            }
            if (!routed.escalate && !routed.text.isEmpty()) {
                final String ans = routed.text;
                final String tier = routed.tier;
                ui.post(() -> {
                    status(ans, Ui.TEXT);
                    setTalkState("SPEAKING", tier);
                });
                try {
                    speak.say(ans, 120);
                } catch (Throwable e) {
                    Log.w(Listener.TAG, "speak failed", e);
                }
                finish_(null, 0);
                return;
            }

            // Only now is this worth an agent's time.
            ui.post(() -> setTalkState("SENDING", "→ " + target));
            double sentAt = System.currentTimeMillis() / 1000.0;
            try {
                Api.send(target, said, true);
            } catch (Exception e) {
                Log.w(Listener.TAG, "send failed", e);
                finish_("could not send to " + target + " — " + short_(e), Ui.ERR);
                return;
            }

            ui.post(() -> setTalkState("WAITING", target + " is thinking"));
            String answer = awaitAnswer(sentAt);
            if (answer == null) {
                finish_(target + " has not answered yet — it is in the transcript"
                        + " if it lands later", Ui.WARN);
                return;
            }

            final String ans = answer;
            ui.post(() -> {
                status(target + ": " + ans, Ui.TEXT);
                setTalkState("SPEAKING", "");
            });
            try {
                speak.say(ans, 120);
            } catch (Throwable e) {
                Log.w(Listener.TAG, "speak failed", e);
            }
            finish_(null, 0);
        });
    }

    /**
     * Poll the conversation for the first INBOUND line after our send.
     *
     * Polling rather than a push, because the board already polls this way and
     * an agent's reply arrives through the mailbox, not through our socket.
     * `since` is the moment we sent, so a reply to an earlier turn can never be
     * mistaken for the answer to this one.
     */
    private String awaitAnswer(double since) {
        long deadline = System.currentTimeMillis() + ANSWER_WAIT_S * 1000L;
        while (System.currentTimeMillis() < deadline) {
            try {
                Thread.sleep(1500);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return null;
            }
            if (!visible) {
                // The person walked away. Keep waiting — they will get the
                // answer in the transcript — but stop holding the speaker.
                return null;
            }
            try {
                for (Api.Entry e : Api.conversation(target, since)) {
                    if (e.inbound() && e.ts > since && !e.text.trim().isEmpty()) {
                        return e.text.trim();
                    }
                }
            } catch (Exception e) {
                Log.w(Listener.TAG, "poll failed", e);
            }
        }
        return null;
    }

    private void setTalkState(String label, String sub) {
        ui.post(() -> {
            talkLabel.setText(label);
            talkSub.setText(sub);
            boolean idle = "TALK".equals(label);
            talkCard.setBackground(Ui.round(idle ? Ui.ACCENT : Ui.LIVE,
                                            Ui.dp(this, 20)));
        });
    }

    private void finish_(String msg, int color) {
        busy = false;
        ui.post(() -> {
            if (msg != null) status(msg, color);
            talkLabel.setText("TALK");
            talkSub.setText("→ " + target);
            talkCard.setBackground(Ui.round(Ui.ACCENT, Ui.dp(this, 20)));
        });
    }

    private void status(String s, int color) {
        statusLine.setText(s);
        statusLine.setTextColor(color == 0 ? Ui.DIM : color);
    }

    // ------------------------------------------------------- notifications

    private void renderNotifications() {
        notifList.removeAllViews();
        Listener l = Listener.get();
        if (l == null || !Listener.isConnected()) {
            notifCount.setText("");
            notifList.addView(note("the notification listener is not bound", Ui.WARN));
            return;
        }
        List<Map<String, Object>> rows;
        try {
            rows = l.list();
        } catch (Exception e) {
            notifList.addView(note("could not read notifications — " + short_(e), Ui.ERR));
            return;
        }
        int shown = 0;
        for (Map<String, Object> r : rows) {
            if (Boolean.TRUE.equals(r.get("group_summary"))) continue;
            String title = str(r.get("title"));
            String text = str(r.get("text"));
            if (title.isEmpty() && text.isEmpty()) continue;
            if (shown >= 8) break;
            notifList.addView(notifRow(r, title, text),
                              Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT));
            shown++;
        }
        notifCount.setText(rows.size() + " active");
        if (shown == 0) notifList.addView(note("nothing on the shade", Ui.DIM));
    }

    private View notifRow(Map<String, Object> r, String title, String text) {
        LinearLayout c = Ui.column(this);
        c.setBackground(Ui.round(Ui.CARD, Ui.dp(this, 14), Ui.dp(this, 1), Ui.LINE));
        int p = Ui.dp(this, 14);
        c.setPadding(p, p, p, p);

        String pkg = str(r.get("package"));
        c.addView(Ui.text(this, shortPkg(pkg), 11, Ui.DIM), Ui.lpWrap());
        if (!title.isEmpty()) {
            c.addView(Ui.text(this, title, 16, Ui.TEXT), Ui.lpWrap());
        }
        if (!text.isEmpty()) {
            TextView t = Ui.text(this, text, 13, Ui.DIM);
            t.setMaxLines(3);
            c.addView(t, Ui.lpWrap());
        }

        if (Boolean.TRUE.equals(r.get("repliable"))) {
            TextView rep = Ui.text(this, "reply", 13, Ui.ACCENT);
            rep.setPadding(0, Ui.dp(this, 10), 0, 0);
            final String key = str(r.get("key"));
            final String who = title.isEmpty() ? shortPkg(pkg) : title;
            rep.setOnClickListener(v -> replyDialog(key, who));
            c.addView(rep, Ui.lpWrap());
        }

        LinearLayout.LayoutParams lp = Ui.lpMatch(ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, Ui.dp(this, 8));
        c.setLayoutParams(lp);
        return c;
    }

    /**
     * Reply, with a confirm step, because a notification reply SENDS — there
     * is no draft on the other side and no way to take it back. The phone verb
     * layer holds the same rule for the same reason.
     */
    private void replyDialog(final String key, String who) {
        final EditText box = new EditText(this);
        box.setInputType(InputType.TYPE_CLASS_TEXT
                         | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
                         | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        box.setHint("reply to " + who);
        int p = Ui.dp(this, 20);
        box.setPadding(p, p / 2, p, p / 2);
        new AlertDialog.Builder(this)
            .setTitle("reply to " + who)
            .setMessage("This sends immediately. There is no draft step.")
            .setView(box)
            .setNegativeButton("cancel", null)
            .setPositiveButton("send", (d, w) -> {
                final String txt = box.getText().toString().trim();
                if (txt.isEmpty()) {
                    status("nothing to send", Ui.DIM);
                    return;
                }
                work.execute(() -> {
                    try {
                        Listener l = Listener.get();
                        if (l == null) throw new IllegalStateException("listener not bound");
                        l.reply(key, txt);
                        ui.post(() -> status("sent: " + txt, Ui.TEXT));
                    } catch (Exception e) {
                        Log.w(Listener.TAG, "reply failed", e);
                        ui.post(() -> status("reply failed — " + short_(e), Ui.ERR));
                    }
                });
            })
            .show();
    }

    // ------------------------------------------------------------- plumbing

    private TextView note(String s, int color) {
        TextView t = Ui.text(this, s, 13, color);
        t.setPadding(Ui.dp(this, 4), Ui.dp(this, 6), Ui.dp(this, 4), Ui.dp(this, 6));
        return t;
    }

    private static String str(Object o) {
        return o == null ? "" : o.toString();
    }

    /** com.google.android.youtube -> youtube. The full id is noise on a row. */
    private static String shortPkg(String pkg) {
        if (pkg == null || pkg.isEmpty()) return "";
        int i = pkg.lastIndexOf('.');
        return i < 0 ? pkg : pkg.substring(i + 1);
    }

    /** An exception as one readable clause, since it goes on screen. */
    private static String short_(Throwable e) {
        String m = e.getMessage();
        if (m == null || m.isEmpty()) return e.getClass().getSimpleName();
        return m.length() > 160 ? m.substring(0, 160) + "…" : m;
    }
}
