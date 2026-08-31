package com.aadarwal.vdisplay;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;

import java.io.BufferedReader;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;

/**
 * vdisplay — hold a virtual display open, so more than one agent can drive the
 * phone at once.
 *
 * WHY A DEX AND NOT AN APP. The display has to be TRUSTED to be worth having:
 * an untrusted virtual display gets no IME, no own focus, and no system
 * decorations (DisplayManagerService strips those flags silently). TRUSTED
 * needs android.permission.ADD_TRUSTED_DISPLAY, which is signature|role — an
 * app cannot be granted it, and `pm grant` refuses it as "not a changeable
 * permission type".
 *
 * But com.android.shell already HOLDS it (packages/Shell/AndroidManifest.xml,
 * android16-release), and uid 2000 is exactly what `phone sh` already runs as
 * via Shizuku. So the display is created by code running as shell, through
 * app_process, and the whole permission question disappears. This is the same
 * mechanism scrcpy uses for --new-display; it is not a bypass, it is a uid.
 *
 * WHY IT STAYS ALIVE. A VirtualDisplay is owned by the process that created
 * it and is released when that process dies. So this cannot be a one-shot
 * command; it is a holder. It idles on a Looper and answers a LocalSocket.
 *
 * WHY AN ImageReader AND NOT NOTHING. The display needs a Surface to render
 * into, and `screencap -d` cannot read it back: screencap works on
 * SurfaceFlinger's PHYSICAL display ids, a different namespace from the
 * WM/AM ids that `am start --display` and `input -d` use, and a virtual
 * display has no physical id at all. Reading our own Surface is the only way
 * to get pixels off it. It also has to be DRAINED whether or not anyone wants
 * a screenshot: an ImageReader whose images are never acquired stops the
 * producer once maxImages are outstanding, and the display would freeze.
 *
 * Protocol on the socket (abstract namespace, one line per request):
 *   info          -> "<id> <w>x<h>/<dpi>"
 *   cap <path>    -> "ok <path>" after a PNG of the latest frame is written
 *   bye           -> "ok", then the process exits and the display goes away
 */
public final class Main {

    // VirtualDisplay flags. The ones that matter here are @hidden in
    // android.jar, so they are spelled as bit positions rather than linked
    // against symbols the SDK does not export. Values from
    // frameworks/base/core/java/android/hardware/display/DisplayManager.java.
    private static final int PUBLIC = 1 << 0;
    private static final int OWN_CONTENT_ONLY = 1 << 3;
    private static final int DESTROY_CONTENT_ON_REMOVAL = 1 << 8;
    private static final int SHOULD_SHOW_SYSTEM_DECORATIONS = 1 << 9;
    private static final int TRUSTED = 1 << 10;
    private static final int OWN_DISPLAY_GROUP = 1 << 11;
    private static final int ALWAYS_UNLOCKED = 1 << 12;
    private static final int TOUCH_FEEDBACK_DISABLED = 1 << 13;
    private static final int OWN_FOCUS = 1 << 14;
    private static final int DEVICE_DISPLAY_GROUP = 1 << 15;

    private static ImageReader reader;
    private static VirtualDisplay display;
    private static final Object FRAME = new Object();
    private static Image latest;
    private static int width = 1080;
    private static int height = 2400;
    private static int density = 420;

    public static void main(String[] args) {
        String name = "phone-vd";
        boolean decorations = true;
        String sockName = null;

        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if (a.equals("--size") && i + 1 < args.length) {
                String[] wh = args[++i].split("x");
                width = Integer.parseInt(wh[0]);
                height = Integer.parseInt(wh[1]);
            } else if (a.equals("--dpi") && i + 1 < args.length) {
                density = Integer.parseInt(args[++i]);
            } else if (a.equals("--name") && i + 1 < args.length) {
                name = args[++i];
            } else if (a.equals("--socket") && i + 1 < args.length) {
                sockName = args[++i];
            } else if (a.equals("--no-decorations")) {
                decorations = false;
            }
        }

        try {
            if (args.length > 0 && args[0].equals("--whoami")) {
                whoami();
                return;
            }
            run(name, decorations, sockName);
        } catch (Throwable t) {
            // The whole point of this process is to report an id on stdout.
            // A stack trace on stderr with nothing on stdout is how the caller
            // learns it failed, so let it through rather than swallowing it.
            System.err.println("vdisplay: " + t);
            t.printStackTrace();
            System.exit(1);
        }
    }

    private static void run(String name, boolean decorations, String sockName)
            throws Exception {
        Looper.prepareMainLooper();

        Context ctx = new FakeContext(systemContext());
        DisplayManager dm = displayManager(ctx);

        HandlerThread ht = new HandlerThread("vdisplay-frames");
        ht.start();
        final Handler handler = new Handler(ht.getLooper());

        reader = ImageReader.newInstance(width, height,
                PixelFormat.RGBA_8888, 3);
        // Retain the newest frame, close the one it replaces.
        //
        // The first version closed every image as it arrived, which kept the
        // producer unblocked and left `cap` with nothing: a virtual display
        // renders only when its content CHANGES, so on a settled screen there
        // is no next frame to wait for and "no frame available yet" was the
        // permanent answer rather than a transient one. Holding one image
        // costs one buffer of the three and makes the last rendered state
        // readable for as long as it is the current state.
        reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
            @Override
            public void onImageAvailable(ImageReader r) {
                try {
                    Image img = r.acquireLatestImage();
                    if (img == null) {
                        return;
                    }
                    synchronized (FRAME) {
                        if (latest != null) {
                            latest.close();
                        }
                        latest = img;
                    }
                } catch (Throwable ignored) {
                    // A closed reader during shutdown is not an error.
                }
            }
        }, handler);

        // DESTROY_CONTENT_ON_REMOVAL, measured and then added: without it,
        // releasing a display MIGRATES its activities to the built-in screen.
        // Killing two holders put Settings in front of the person's launcher
        // with nothing to explain why. A display an agent was given should
        // take its work with it when it goes.
        int flags = PUBLIC | OWN_CONTENT_ONLY | TRUSTED | OWN_DISPLAY_GROUP
                | ALWAYS_UNLOCKED | TOUCH_FEEDBACK_DISABLED | OWN_FOCUS
                | DEVICE_DISPLAY_GROUP | DESTROY_CONTENT_ON_REMOVAL;
        if (decorations) {
            // Without this the display has no launcher and no status bar. An
            // activity started with `am start --display` still lands, so this
            // is optional — but a display with decorations behaves like a
            // screen, and one without behaves like a surface.
            flags |= SHOULD_SHOW_SYSTEM_DECORATIONS;
        }

        display = dm.createVirtualDisplay(name, width, height, density,
                reader.getSurface(), flags);
        if (display == null) {
            throw new IllegalStateException("createVirtualDisplay returned null");
        }
        int id = display.getDisplay().getDisplayId();

        // stdout is the contract with the caller. Flush it: the caller reads a
        // line and then detaches, and a buffered id is an id nobody gets.
        System.out.println("display " + id);
        System.out.flush();

        if (sockName != null) {
            serve(sockName, id);
        } else {
            Looper.loop();
        }
    }

    /**
     * A DisplayManager that reports OUR package name.
     *
     * ctx.getSystemService() will not do. ContextWrapper delegates it to the
     * BASE context, so the DisplayManager comes back holding the system
     * context — whose package is "android", owned by uid 1000 — and the
     * FakeContext wrapping it is never consulted again. The service then sends
     * "android" from a uid-2000 caller and the server rejects it:
     *
     *     SecurityException: packageName must match the owner uid
     *
     * which reads like a permission failure and is not one. Constructing the
     * manager against the wrapper directly is what makes the package name we
     * chose the package name that travels.
     */
    private static DisplayManager displayManager(Context ctx) throws Exception {
        Constructor<DisplayManager> c =
                DisplayManager.class.getDeclaredConstructor(Context.class);
        c.setAccessible(true);
        DisplayManager dm = c.newInstance(ctx);
        if (dm == null) {
            throw new IllegalStateException("no DisplayManager");
        }
        return dm;
    }

    /**
     * The system Context, from a process that is not an app.
     *
     * Two routes because they fail on different devices and neither is API.
     * systemMain() is the documented-by-usage path and does the fuller setup;
     * the bare constructor is the fallback for when systemMain() insists on
     * being the system process. Whichever produces a Context wins.
     */
    private static Context systemContext() throws Exception {
        Class<?> at = Class.forName("android.app.ActivityThread");
        Throwable first = null;
        try {
            Method systemMain = at.getMethod("systemMain");
            Object thread = systemMain.invoke(null);
            Method get = at.getMethod("getSystemContext");
            Context ctx = (Context) get.invoke(thread);
            if (ctx != null) {
                return ctx;
            }
        } catch (Throwable t) {
            first = t;
        }
        try {
            Constructor<?> c = at.getDeclaredConstructor();
            c.setAccessible(true);
            Object thread = c.newInstance();
            Method get = at.getDeclaredMethod("getSystemContext");
            get.setAccessible(true);
            Context ctx = (Context) get.invoke(thread);
            if (ctx != null) {
                return ctx;
            }
        } catch (Throwable t) {
            if (first != null) {
                t.addSuppressed(first);
            }
            throw new IllegalStateException("no system context", t);
        }
        throw new IllegalStateException("no system context", first);
    }

    private static void serve(String sockName, int id) throws Exception {
        LocalServerSocket server = new LocalServerSocket(sockName);
        while (true) {
            LocalSocket s = server.accept();
            try {
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(s.getInputStream(), "UTF-8"));
                OutputStream out = s.getOutputStream();
                String line = in.readLine();
                String reply = handle(line, id);
                out.write((reply + "\n").getBytes("UTF-8"));
                out.flush();
                if (line != null && line.trim().equals("bye")) {
                    s.close();
                    server.close();
                    // Releasing explicitly rather than relying on process exit
                    // so the display is gone before the caller's next command.
                    if (display != null) {
                        display.release();
                    }
                    System.exit(0);
                }
            } catch (Throwable t) {
                System.err.println("vdisplay: " + t);
            } finally {
                try {
                    s.close();
                } catch (Throwable ignored) {
                    // Already closed on the `bye` path.
                }
            }
        }
    }

    private static String handle(String line, int id) {
        if (line == null) {
            return "err empty";
        }
        String cmd = line.trim();
        if (cmd.equals("info")) {
            return id + " " + width + "x" + height + "/" + density;
        }
        if (cmd.equals("bye")) {
            return "ok";
        }
        if (cmd.startsWith("cap ")) {
            String path = cmd.substring(4).trim();
            try {
                capture(path);
                return "ok " + path;
            } catch (Throwable t) {
                return "err " + t;
            }
        }
        return "err unknown " + cmd;
    }

    /**
     * A PNG of the newest frame.
     *
     * acquireLatestImage() can legitimately return null — the display renders
     * only when something on it changes, so a still screen produces no new
     * buffer. Reporting that as an error is right: a caller that gets a stale
     * frame and believes it is current is the screen tier's worst failure.
     */
    private static void capture(String path) throws Exception {
        synchronized (FRAME) {
            Image img = latest;
            if (img == null) {
                throw new IllegalStateException("no frame rendered yet");
            }
            encode(img, path);
        }
    }

    private static void encode(Image img, String path) throws Exception {
        {
            Image.Plane plane = img.getPlanes()[0];
            ByteBuffer buf = plane.getBuffer();
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            // Row stride is almost never width*4: the buffer is padded to the
            // GPU's alignment. Allocating to the padded width and cropping is
            // the only way to read it back without shearing the image.
            int padded = rowStride / pixelStride;
            Bitmap full = Bitmap.createBitmap(padded, img.getHeight(),
                    Bitmap.Config.ARGB_8888);
            full.copyPixelsFromBuffer(buf);
            Bitmap out = Bitmap.createBitmap(full, 0, 0,
                    img.getWidth(), img.getHeight());
            FileOutputStream fos = new FileOutputStream(path);
            try {
                out.compress(Bitmap.CompressFormat.PNG, 100, fos);
                fos.flush();
            } finally {
                fos.close();
            }
            if (out != full) {
                out.recycle();
            }
            full.recycle();
        }
    }

    /**
     * What identity this process actually presents. The first attempt at this
     * class failed on a package/uid mismatch that no amount of reading the
     * source settled, so the check is a verb now.
     */
    private static void whoami() throws Exception {
        Looper.prepareMainLooper();
        Context base = systemContext();
        Context ctx = new FakeContext(base);
        System.out.println("uid           " + android.os.Process.myUid());
        System.out.println("base pkg      " + base.getPackageName());
        System.out.println("fake pkg      " + ctx.getPackageName());
        System.out.println("fake op pkg   " + ctx.getOpPackageName());
        String[] pkgs = ctx.getPackageManager()
                .getPackagesForUid(android.os.Process.myUid());
        System.out.println("pkgs for uid  "
                + (pkgs == null ? "null" : java.util.Arrays.toString(pkgs)));
    }
}
