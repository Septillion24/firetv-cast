package com.firetvcast.helper;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.os.Handler;
import android.os.SystemClock;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * Swaps Amazon's home screen for ours. Fire OS won't let another app be the default home (it overrides the
 * preferred-home setting) and keeps the Home button to itself (no key filtering, no "homekey" broadcast for
 * other apps), so this opens Home whenever Amazon's home screen comes up: Home button, power on, leaving an
 * app. The service's settings are in res/xml/home_keeper.xml.
 *
 * Amazon's Settings is a page of that same screen. When Home opens it, this keeps reading the screen and
 * opens Home as soon as it shows something other than Settings (the Home button there sends no event).
 *
 * Turn on from adb (turn it off and on again after reinstalling the app, so the new settings are read):
 *   settings put secure enabled_accessibility_services com.firetvcast.helper/com.firetvcast.helper.HomeKeeper
 *   settings put secure accessibility_enabled 1
 * Turn off: settings delete secure enabled_accessibility_services
 */
public class HomeKeeper extends AccessibilityService {
    private static final String TAG = "HomeKeeper";
    private static final String AMAZON_LAUNCHER = "com.amazon.tv.launcher";
    /** Settings pages such as Network and Applications are their own app. */
    private static final String AMAZON_SETTINGS = "com.amazon.tv.settings.v2";
    /** Amazon's home screen activity (Settings is a page of it). */
    private static final String AMAZON_HOME_ACTIVITY = "HomeActivity";
    /** The search button at the top of Amazon's Settings page (English Fire OS). */
    private static final String SETTINGS_MARKER = "Search Your Settings";
    private static final long MIN_GAP_MS = 1000;
    private static final long WATCH_EVERY_MS = 700;

    /** How long Amazon's screen is left alone after Home opens it on purpose, while it comes up. */
    private static final long AMAZON_GRACE_MS = 3000;
    private static volatile long amazonAllowedUntil;
    private static volatile boolean settingsOpen;

    private final Executor launcher = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler();
    private long lastRedirect;

    private final Runnable watch = new Runnable() {
        @Override
        public void run() {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            String screen = root == null || root.getPackageName() == null ? null : root.getPackageName().toString();
            boolean keepWatching = screen == null || screen.equals(AMAZON_SETTINGS)
                    || (screen.equals(AMAZON_LAUNCHER) && (allowedNow() || showsSettings(root)));
            if (root != null) {
                root.recycle();
            }
            if (keepWatching) {
                handler.postDelayed(this, WATCH_EVERY_MS);
                return;
            }
            settingsOpen = false;
            if (AMAZON_LAUNCHER.equals(screen)) {
                redirect();
            }
        }
    };

    /** Amazon's home screen may already be up when this starts (after power on, or an update of this app). */
    @Override
    protected void onServiceConnected() {
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                AccessibilityNodeInfo root = getRootInActiveWindow();
                if (root == null) {
                    return;
                }
                if (AMAZON_LAUNCHER.equals(String.valueOf(root.getPackageName())) && !showsSettings(root)) {
                    redirect();
                }
                root.recycle();
            }
        }, 500);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        CharSequence screen = event.getClassName();
        if (screen == null || !screen.toString().contains(AMAZON_HOME_ACTIVITY)) {
            return;
        }
        if (settingsOpen) {
            // Coming up for Settings (or back to it from a Settings page): watch it instead.
            handler.removeCallbacks(watch);
            handler.post(watch);
        } else if (!allowedNow()) {
            redirect();
        }
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(watch);
        super.onDestroy();
    }

    /** Called by Home just before it opens Amazon's screen on purpose: its Settings, or its home screen. */
    static void allowAmazonScreen(boolean settings) {
        amazonAllowedUntil = SystemClock.uptimeMillis() + AMAZON_GRACE_MS;
        settingsOpen = settings;
    }

    private static boolean allowedNow() {
        return SystemClock.uptimeMillis() < amazonAllowedUntil;
    }

    private static boolean showsSettings(AccessibilityNodeInfo root) {
        return !root.findAccessibilityNodeInfosByText(SETTINGS_MARKER).isEmpty();
    }

    /** Amazon's screen may announce itself more than once as it comes up; act once. */
    private void redirect() {
        long now = SystemClock.uptimeMillis();
        if (now - lastRedirect >= MIN_GAP_MS) {
            lastRedirect = now;
            openHome();
        }
    }

    /**
     * Opens Home. After the Home button Android holds that back for 5 seconds, unless ShellHelper (started
     * by the PC) lifts the hold first; without it Home still comes, just late.
     */
    private void openHome() {
        launcher.execute(new Runnable() {
            @Override
            public void run() {
                Socket helper = new Socket();
                try {
                    helper.connect(new InetSocketAddress("127.0.0.1", ShellHelper.PORT), 500);
                    helper.setSoTimeout(1000);
                    helper.getOutputStream().write(1);
                    helper.getInputStream().read();
                } catch (IOException error) {
                    Log.i(TAG, "Shell helper not running; Home may take 5 seconds (" + error + ")");
                } finally {
                    try {
                        helper.close();
                    } catch (IOException ignored) {
                    }
                }
                startActivity(new Intent(HomeKeeper.this, Home.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_NO_ANIMATION));
            }
        });
    }
}
