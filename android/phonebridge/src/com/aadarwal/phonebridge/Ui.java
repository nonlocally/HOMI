package com.aadarwal.phonebridge;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Views built in code, because this app ships no resources.
 *
 * That constraint is deliberate and it is what keeps the build to four tool
 * invocations and no Gradle — but it means there is no styles.xml to hold the
 * look, so it lives here instead. One place, so the screen stays coherent
 * without a theme.
 */
final class Ui {

    // A dark palette on purpose. This is a thing you open in the dark, next to
    // a person who is about to talk to it, and a white page at 2am is hostile.
    static final int BG        = Color.parseColor("#0d0f12");
    static final int CARD      = Color.parseColor("#16191e");
    static final int CARD_HI   = Color.parseColor("#1d2128");
    static final int LINE      = Color.parseColor("#262b33");
    static final int TEXT      = Color.parseColor("#e8eaed");
    static final int DIM       = Color.parseColor("#8b93a1");
    static final int ACCENT    = Color.parseColor("#7aa2f7");
    static final int LIVE      = Color.parseColor("#4ade80");
    static final int IDLE      = Color.parseColor("#4b5361");
    static final int WARN      = Color.parseColor("#f0a14e");
    static final int ERR       = Color.parseColor("#f4707a");

    private Ui() {}

    static int dp(Context c, float v) {
        return Math.round(TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v, c.getResources().getDisplayMetrics()));
    }

    static LinearLayout column(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }

    static LinearLayout row(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        return l;
    }

    static TextView text(Context c, String s, int size, int color) {
        TextView t = new TextView(c);
        t.setText(s == null ? "" : s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, size);
        t.setTextColor(color);
        return t;
    }

    /** A rounded solid block — the only "shape resource" this app has. */
    static GradientDrawable round(int fill, int radiusPx) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.RECTANGLE);
        g.setColor(fill);
        g.setCornerRadius(radiusPx);
        return g;
    }

    static GradientDrawable round(int fill, int radiusPx, int strokePx, int stroke) {
        GradientDrawable g = round(fill, radiusPx);
        g.setStroke(strokePx, stroke);
        return g;
    }

    /** A small filled circle, for state dots. */
    static GradientDrawable dot(int fill) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(fill);
        return g;
    }

    static LinearLayout.LayoutParams lp(int w, int h) {
        return new LinearLayout.LayoutParams(w, h);
    }

    static LinearLayout.LayoutParams lpMatch(int h) {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, h);
    }

    static LinearLayout.LayoutParams lpWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    /** Grow to fill the leftover space in a row. */
    static LinearLayout.LayoutParams lpGrow() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.weight = 1;
        return p;
    }

    static void margins(View v, int l, int t, int r, int b) {
        ViewGroup.LayoutParams p = v.getLayoutParams();
        if (p instanceof ViewGroup.MarginLayoutParams) {
            ((ViewGroup.MarginLayoutParams) p).setMargins(l, t, r, b);
        }
    }

    /**
     * Pad a view by the system bars.
     *
     * Not optional and not cosmetic: from targetSdk 35 Android draws every app
     * edge to edge whether it asked to or not, so a NoActionBar theme is no
     * longer enough — the first build of this screen put the word "homi"
     * underneath the clock. The theme did not change; the platform did.
     */
    static void fitSystemBars(final View v, final int extraTop,
                              final int extraBottom) {
        v.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(
                android.view.WindowInsets.Type.systemBars()
                | android.view.WindowInsets.Type.displayCutout());
            view.setPadding(view.getPaddingLeft(), bars.top + extraTop,
                            view.getPaddingRight(), bars.bottom + extraBottom);
            return insets;
        });
        v.requestApplyInsets();
    }

    /** A section heading: small, spaced, dim. */
    static TextView heading(Context c, String s) {
        TextView t = text(c, s.toUpperCase(java.util.Locale.US), 11, DIM);
        t.setLetterSpacing(0.14f);
        t.setPadding(dp(c, 4), dp(c, 18), 0, dp(c, 8));
        return t;
    }
}
