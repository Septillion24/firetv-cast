package com.firetvcast.helper;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.AnimatorSet;
import android.animation.ObjectAnimator;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.tv.TvInputInfo;
import android.media.tv.TvInputManager;
import android.os.Bundle;
import android.os.Handler;
import android.text.SpannableString;
import android.text.format.DateFormat;
import android.text.style.RelativeSizeSpan;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.LinearInterpolator;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * The home screen, as a photo frame: nature photos fade into one another behind a clock, and the menu (inputs,
 * apps, Settings, Amazon's app grid for the Appstore) only appears when a button is pressed. No ads, rows or
 * autoplay. HomeKeeper opens it in place of Amazon's.
 *
 * The photos and their credits come from tv-helper/photos.py, which copies them into this app's storage
 * (Android/data/com.firetvcast.helper/files/photos). Built in code (the helper has no layout resources).
 */
public class Home extends Activity {
    private static final int CREAM = 0xFFF5F0E6;
    private static final int CREAM_SOFT = 0xC8F5F0E6;
    private static final int CREAM_FAINT = 0x66F5F0E6;
    private static final int SHADOW = 0x99000000;

    private static final long PHOTO_MS = 75000;
    private static final long FADE_MS = 2500;
    private static final long MENU_HIDE_MS = 15000;
    private static final int MENU_HEIGHT = 64;
    private static final int ITEM_PAD = 14;
    /** The photo slowly zooms between these scales and drifts sideways by up to DRIFT dp. */
    private static final float ZOOM_FROM = 1.05f;
    private static final float ZOOM_TO = 1.10f;
    private static final int DRIFT = 16;

    private static final String LEANBACK_LAUNCHER = "android.intent.category.LEANBACK_LAUNCHER";
    private static final String[] APP_GRID = {"com.amazon.venezia", "com.amazon.venezia.grid.AppsGridLauncherActivity"};
    private static final String KEY_LAST = "last";
    private static final String KEY_PHOTO = "photo";
    private static final String KEY_SEED = "seed";

    private final Handler handler = new Handler();
    private final Executor loader = Executors.newSingleThreadExecutor();
    private final Random random = new Random();
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            showTime();
            handler.postDelayed(this, 10000);
        }
    };
    private final Runnable nextPhoto = new Runnable() {
        @Override
        public void run() {
            advancePhoto(false);
        }
    };
    private final Runnable autoHide = new Runnable() {
        @Override
        public void run() {
            hideMenu(true);
        }
    };

    private SharedPreferences prefs;
    private FrameLayout stage;
    private ImageView front;
    private ImageView back;
    private AnimatorSet frontDrift;
    private View menuShade;
    private LinearLayout info;
    private TextView clock;
    private TextView date;
    private LinearLayout menu;
    private View menuStrip;
    private FrameLayout creditFrame;
    private LinearLayout credit;
    private TextView caption;
    private TextView byline;

    private List<File> photos = new ArrayList<File>();
    private final Map<String, String[]> credits = new HashMap<String, String[]>();
    private boolean started;
    private boolean menuShown;
    private int swallowedKey = -1;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences("home", MODE_PRIVATE);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF0B0B0C);

        stage = new FrameLayout(this);
        front = photoView();
        back = photoView();
        stage.addView(back, new FrameLayout.LayoutParams(-1, -1));
        stage.addView(front, new FrameLayout.LayoutParams(-1, -1));
        root.addView(stage, new FrameLayout.LayoutParams(-1, -1));

        // A soft darkening toward the bottom so the type reads on any photo, and a little more with the menu.
        root.addView(shade(new int[] {0xB3000000, 0x40000000, 0x00000000}), new FrameLayout.LayoutParams(-1, -1));
        menuShade = shade(new int[] {0x99000000, 0x33000000, 0x00000000});
        menuShade.setAlpha(0f);
        root.addView(menuShade, new FrameLayout.LayoutParams(-1, -1));

        FrameLayout overlay = new FrameLayout(this);
        overlay.setPadding(dp(64), dp(40), dp(64), dp(40));
        overlay.setClipToPadding(false);
        overlay.setClipChildren(false);

        info = new LinearLayout(this);
        info.setOrientation(LinearLayout.VERTICAL);
        clock = serif(64, CREAM, Typeface.NORMAL);
        clock.setIncludeFontPadding(false);
        info.addView(clock);
        date = serif(20, CREAM_SOFT, Typeface.ITALIC);
        date.setPadding(dp(2), dp(6), 0, 0);
        info.addView(date);
        HorizontalScrollView scroller = new HorizontalScrollView(this);
        scroller.setHorizontalScrollBarEnabled(false);
        scroller.setOverScrollMode(View.OVER_SCROLL_NEVER);
        menu = new LinearLayout(this);
        menu.setGravity(Gravity.CENTER_VERTICAL);
        scroller.addView(menu, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, -1));
        menuStrip = scroller;
        // Pulled left by the items' padding so the first name lines up with the clock.
        LinearLayout.LayoutParams stripParams = new LinearLayout.LayoutParams(-1, dp(MENU_HEIGHT));
        stripParams.topMargin = dp(18);
        stripParams.leftMargin = -dp(ITEM_PAD);
        info.addView(menuStrip, stripParams);
        overlay.addView(info, new FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.LEFT));

        // The credit fades with each photo; its frame fades out while the menu, which it would cover, is up.
        creditFrame = new FrameLayout(this);
        credit = new LinearLayout(this);
        credit.setOrientation(LinearLayout.VERTICAL);
        credit.setGravity(Gravity.RIGHT);
        caption = serif(15, CREAM_SOFT, Typeface.ITALIC);
        credit.addView(caption, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        byline = serif(11, CREAM_FAINT, Typeface.NORMAL);
        byline.setPadding(0, dp(3), 0, 0);
        credit.addView(byline, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        creditFrame.addView(credit);
        overlay.addView(creditFrame, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.RIGHT));

        root.addView(overlay, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
    }

    @Override
    protected void onStart() {
        super.onStart();
        started = true;
        hideMenu(false);
        loadPhotos();
        advancePhoto(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        buildMenu();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(tick);
        super.onPause();
    }

    @Override
    protected void onStop() {
        started = false;
        handler.removeCallbacks(nextPhoto);
        handler.removeCallbacks(autoHide);
        super.onStop();
    }

    /**
     * While the menu is hidden, the first press only brings it up. Volume, media and other keys pass through.
     * Back hides it again (and never brings it up, since Back is also how people leave apps to come here); so
     * does a while without presses.
     */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int code = event.getKeyCode();
        if (code == KeyEvent.KEYCODE_BACK) {
            if (menuShown && event.getAction() == KeyEvent.ACTION_UP) {
                hideMenu(true);
            }
            return true;
        }
        if (code == swallowedKey) {
            if (event.getAction() == KeyEvent.ACTION_UP) {
                swallowedKey = -1;
            }
            return true;
        }
        if (!revealsMenu(code)) {
            return super.dispatchKeyEvent(event);
        }
        if (!menuShown) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                showMenu();
                swallowedKey = code;
            }
            return true;
        }
        handler.removeCallbacks(autoHide);
        handler.postDelayed(autoHide, MENU_HIDE_MS);
        return super.dispatchKeyEvent(event);
    }

    private static boolean revealsMenu(int code) {
        switch (code) {
            case KeyEvent.KEYCODE_DPAD_UP:
            case KeyEvent.KEYCODE_DPAD_DOWN:
            case KeyEvent.KEYCODE_DPAD_LEFT:
            case KeyEvent.KEYCODE_DPAD_RIGHT:
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_MENU:
                return true;
            default:
                return false;
        }
    }

    private void showMenu() {
        menuShown = true;
        View last = menu.findViewWithTag(prefs.getString(KEY_LAST, ""));
        if (last == null || !last.requestFocus()) {
            menu.requestFocus();
        }
        DecelerateInterpolator ease = new DecelerateInterpolator();
        info.animate().translationY(0).setDuration(450).setStartDelay(0).setInterpolator(ease).start();
        menuStrip.animate().alpha(1f).setDuration(450).setStartDelay(120).setInterpolator(ease).start();
        menuShade.animate().alpha(1f).setDuration(450).start();
        creditFrame.animate().alpha(0f).setDuration(250).start();
        handler.removeCallbacks(autoHide);
        handler.postDelayed(autoHide, MENU_HIDE_MS);
    }

    private void hideMenu(boolean animate) {
        menuShown = false;
        handler.removeCallbacks(autoHide);
        float lowered = dp(MENU_HEIGHT + 18);
        long duration = animate ? 600 : 0;
        DecelerateInterpolator ease = new DecelerateInterpolator();
        menuStrip.animate().alpha(0f).setDuration(animate ? 300 : 0).setStartDelay(0).start();
        info.animate().translationY(lowered).setDuration(duration).setStartDelay(animate ? 150 : 0)
                .setInterpolator(ease).start();
        menuShade.animate().alpha(0f).setDuration(duration).start();
        creditFrame.animate().alpha(1f).setDuration(duration).start();
    }

    // ---- Photos

    private ImageView photoView() {
        ImageView view = new ImageView(this);
        view.setScaleType(ImageView.ScaleType.CENTER_CROP);
        return view;
    }

    private View shade(int[] bottomToTop) {
        View view = new View(this);
        view.setBackgroundDrawable(new GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP, bottomToTop));
        return view;
    }

    /** Lists the photos, in a shuffled order that stays the same until it has gone all the way round. */
    private void loadPhotos() {
        File dir = new File(getExternalFilesDir(null), "photos");
        File[] files = dir.listFiles();
        photos = new ArrayList<File>();
        if (files != null) {
            for (File file : files) {
                if (file.getName().endsWith(".jpg")) {
                    photos.add(file);
                }
            }
        }
        Collections.sort(photos);
        Collections.shuffle(photos, new Random(prefs.getLong(KEY_SEED, 1)));
        credits.clear();
        try {
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                    new FileInputStream(new File(dir, "credits.tsv")), "UTF-8"));
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] fields = line.split("\t");
                    if (fields.length == 4) {
                        credits.put(fields[0], Arrays.copyOfRange(fields, 1, 4));
                    }
                }
            } finally {
                reader.close();
            }
        } catch (IOException ignored) {
            // No credits file: the photos show without captions.
        }
    }

    /** Shows the next photo (or, on `first`, the current one at once) and schedules the one after. */
    private void advancePhoto(final boolean first) {
        handler.removeCallbacks(nextPhoto);
        if (photos.isEmpty()) {
            return;
        }
        int index = prefs.getInt(KEY_PHOTO, 0);
        if (!first) {
            index++;
            if (index >= photos.size()) {
                index = 0;
                prefs.edit().putLong(KEY_SEED, random.nextLong()).apply();
                loadPhotos();
            }
            prefs.edit().putInt(KEY_PHOTO, index).apply();
        }
        final File file = photos.get(Math.min(index, photos.size() - 1));
        loader.execute(new Runnable() {
            @Override
            public void run() {
                final Bitmap bitmap = BitmapFactory.decodeFile(file.getPath());
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        if (started && bitmap != null) {
                            reveal(bitmap, file.getName(), first);
                        }
                    }
                });
            }
        });
        handler.postDelayed(nextPhoto, PHOTO_MS);
    }

    /** Fades the new photo in over the old one, starts its slow drift, and swaps the credit. */
    private void reveal(Bitmap bitmap, String name, boolean instant) {
        final ImageView incoming = back;
        final ImageView outgoing = front;
        final AnimatorSet outgoingDrift = frontDrift;
        incoming.setImageBitmap(bitmap);
        stage.bringChildToFront(incoming);
        frontDrift = drift(incoming);
        front = incoming;
        back = outgoing;
        ObjectAnimator fade = ObjectAnimator.ofFloat(incoming, "alpha", instant ? 1f : 0f, 1f);
        fade.setDuration(instant ? 0 : FADE_MS);
        fade.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                if (outgoingDrift != null) {
                    outgoingDrift.cancel();
                }
                outgoing.setImageDrawable(null);
            }
        });
        fade.start();

        final String[] about = credits.get(name);
        credit.animate().alpha(0f).setDuration(instant ? 0 : 800).withEndAction(new Runnable() {
            @Override
            public void run() {
                caption.setText(about == null ? "" : about[0]);
                byline.setText(about == null ? "" : "Photograph by " + about[1] + "  ·  " + about[2]);
                credit.animate().alpha(1f).setDuration(1200).start();
            }
        }).start();
    }

    private AnimatorSet drift(View view) {
        float shift = dp(DRIFT) * (random.nextBoolean() ? 1 : -1);
        boolean zoomIn = random.nextBoolean();
        float from = zoomIn ? ZOOM_FROM : ZOOM_TO;
        float to = zoomIn ? ZOOM_TO : ZOOM_FROM;
        AnimatorSet set = new AnimatorSet();
        set.playTogether(
                ObjectAnimator.ofFloat(view, "scaleX", from, to),
                ObjectAnimator.ofFloat(view, "scaleY", from, to),
                ObjectAnimator.ofFloat(view, "translationX", -shift, shift));
        set.setDuration(PHOTO_MS + FADE_MS);
        set.setInterpolator(new LinearInterpolator());
        set.start();
        return set;
    }

    // ---- Clock

    private void showTime() {
        // "4:17 pm" with a smaller "pm".
        String time = DateFormat.getTimeFormat(this).format(new Date());
        SpannableString styled = new SpannableString(time.toLowerCase());
        int space = time.lastIndexOf(' ');
        if (space > 0 && !Character.isDigit(time.charAt(time.length() - 1))) {
            styled.setSpan(new RelativeSizeSpan(0.42f), space, time.length(), 0);
        }
        clock.setText(styled);
        date.setText(DateFormat.format("EEEE, d MMMM", new Date()));
    }

    // ---- Menu

    private void buildMenu() {
        menu.removeAllViews();
        List<View> inputs = inputItems();
        for (View item : inputs) {
            menu.addView(item);
        }
        if (!inputs.isEmpty()) {
            menu.addView(divider());
        }
        for (View item : appItems()) {
            menu.addView(item);
        }
        menu.addView(divider());
        // Settings is a page of Amazon's home screen, so HomeKeeper lets that screen through for it.
        menu.addView(amazonItem("settings", "Settings", true,
                new Intent("android.settings.SETTINGS").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
        // Amazon's own app grid, with the Appstore, opens as an ordinary app (no ads, and the Home button
        // leaves it). Without it, fall back to Amazon's home screen, where the Home button does nothing.
        Intent allApps = new Intent(Intent.ACTION_MAIN).setComponent(new ComponentName(APP_GRID[0], APP_GRID[1]))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (getPackageManager().resolveActivity(allApps, 0) != null) {
            menu.addView(item("apps", "All Apps", allApps));
        } else {
            menu.addView(amazonItem("amazon", "Fire TV", false, new Intent(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)));
        }
        if (menuShown) {
            View last = menu.findViewWithTag(prefs.getString(KEY_LAST, ""));
            if (last != null) {
                last.requestFocus();
            }
        }
    }

    private List<View> inputItems() {
        List<View> items = new ArrayList<View>();
        TvInputManager manager = (TvInputManager) getSystemService("tv_input");
        if (manager == null) {
            return items;
        }
        List<TvInputInfo> inputs = new ArrayList<TvInputInfo>(manager.getTvInputList());
        Collections.sort(inputs, new Comparator<TvInputInfo>() {
            @Override
            public int compare(TvInputInfo a, TvInputInfo b) {
                return a.getId().compareTo(b.getId());
            }
        });
        int number = 0;
        for (TvInputInfo input : inputs) {
            // Devices found over HDMI-CEC show up as extra inputs on the same port; keep only the ports.
            if (input.getType() != TvInputInfo.TYPE_HDMI || input.getHdmiDeviceInfo() != null) {
                continue;
            }
            number++;
            if (input.isHidden(this)) {
                continue;
            }
            CharSequence custom = input.loadCustomLabel(this);
            String label = custom != null && custom.length() > 0 ? custom.toString() : "HDMI " + number;
            Intent open = new Intent(this, Hdmi.class)
                    .putExtra(Hdmi.EXTRA_INPUT, input.getId())
                    .putExtra(Hdmi.EXTRA_LABEL, label);
            items.add(item(input.getId(), label, open));
        }
        return items;
    }

    private List<View> appItems() {
        PackageManager pm = getPackageManager();
        Intent query = new Intent(Intent.ACTION_MAIN).addCategory(LEANBACK_LAUNCHER);
        List<ResolveInfo> apps = new ArrayList<ResolveInfo>();
        for (ResolveInfo app : pm.queryIntentActivities(query, 0)) {
            ApplicationInfo info = app.activityInfo.applicationInfo;
            boolean system = (info.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
            if (!system && !info.packageName.equals(getPackageName())) {
                apps.add(app);
            }
        }
        Collections.sort(apps, new ResolveInfo.DisplayNameComparator(pm));
        List<View> items = new ArrayList<View>();
        for (ResolveInfo app : apps) {
            Intent open = new Intent(Intent.ACTION_MAIN).addCategory(LEANBACK_LAUNCHER)
                    .setComponent(new ComponentName(app.activityInfo.packageName, app.activityInfo.name))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            items.add(item(app.activityInfo.packageName, app.loadLabel(pm).toString(), open));
        }
        return items;
    }

    private View amazonItem(String key, String label, final boolean settings, final Intent open) {
        View item = item(key, label, open);
        item.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                HomeKeeper.allowAmazonScreen(settings);
                remember(view);
                startActivity(open);
            }
        });
        return item;
    }

    /** A name in the menu. The chosen one turns bright, with a fine line drawn out beneath it. */
    private View item(String key, String label, final Intent open) {
        LinearLayout item = new LinearLayout(this);
        item.setOrientation(LinearLayout.VERTICAL);
        item.setGravity(Gravity.CENTER_HORIZONTAL);
        item.setTag(key);
        item.setFocusable(true);
        item.setClickable(true);
        item.setPadding(dp(ITEM_PAD), dp(8), dp(ITEM_PAD), dp(8));
        final TextView name = serif(20, CREAM_SOFT, Typeface.NORMAL);
        name.setText(label);
        item.addView(name);
        final View line = new View(this);
        line.setBackgroundColor(CREAM);
        line.setScaleX(0f);
        LinearLayout.LayoutParams lineParams = new LinearLayout.LayoutParams(-1, Math.max(1, dp(1.5f)));
        lineParams.topMargin = dp(6);
        item.addView(line, lineParams);
        item.setOnFocusChangeListener(new View.OnFocusChangeListener() {
            @Override
            public void onFocusChange(View view, boolean focused) {
                name.setTextColor(focused ? CREAM : CREAM_SOFT);
                line.animate().scaleX(focused ? 1f : 0f).setDuration(280)
                        .setInterpolator(new DecelerateInterpolator()).start();
            }
        });
        item.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                remember(view);
                try {
                    startActivity(open);
                } catch (Exception ignored) {
                    // The app was removed since the menu was built; the next refresh drops it.
                }
            }
        });
        return item;
    }

    private View divider() {
        View divider = new View(this);
        divider.setBackgroundColor(CREAM_FAINT);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(Math.max(1, dp(1)), dp(22));
        params.leftMargin = dp(12);
        params.rightMargin = dp(12);
        divider.setLayoutParams(params);
        return divider;
    }

    private void remember(View item) {
        prefs.edit().putString(KEY_LAST, (String) item.getTag()).apply();
    }

    private TextView serif(float sp, int color, int style) {
        TextView view = new TextView(this);
        view.setTypeface(Typeface.create(Typeface.SERIF, style));
        view.setTextSize(sp);
        view.setTextColor(color);
        view.setShadowLayer(dp(6), 0, dp(1), SHADOW);
        return view;
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
