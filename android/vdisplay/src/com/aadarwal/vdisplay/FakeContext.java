package com.aadarwal.vdisplay;

import android.content.AttributionSource;
import android.content.Context;
import android.content.ContextWrapper;
import android.os.Process;

/**
 * A Context that says it is com.android.shell.
 *
 * Not cosmetic. DisplayManagerService.createVirtualDisplayInternal validates
 * the package name against the calling uid:
 *
 *     if (!validatePackageName(callingUid, packageName))
 *         throw new SecurityException("packageName must match the calling uid");
 *
 * We run under app_process at uid 2000 (shell), but the Context that
 * ActivityThread hands out reports its package as "android" — which belongs to
 * uid 1000. Passing that through fails the check, and the failure reads as a
 * permission problem rather than a naming one.
 *
 * com.android.shell is the package that actually owns uid 2000, and it is the
 * package that holds ADD_TRUSTED_DISPLAY (packages/Shell/AndroidManifest.xml,
 * android16-release). So claiming it is not a spoof for privilege — it is
 * naming the identity we already have.
 */
public final class FakeContext extends ContextWrapper {

    public static final String PACKAGE_NAME = "com.android.shell";

    public FakeContext(Context base) {
        super(base);
    }

    @Override
    public String getPackageName() {
        return PACKAGE_NAME;
    }

    @Override
    public String getOpPackageName() {
        return PACKAGE_NAME;
    }

    @Override
    public AttributionSource getAttributionSource() {
        // The uid has to be OURS (shell), not the base context's notion of
        // itself. An AttributionSource whose uid and package disagree fails
        // the same validation this class exists to pass.
        return new AttributionSource.Builder(Process.myUid())
                .setPackageName(PACKAGE_NAME)
                .build();
    }

    @Override
    public Context getApplicationContext() {
        return this;
    }
}
