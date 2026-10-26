package com.firetvcast.helper;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.media.tv.TvInputManager;
import android.media.tv.TvView;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;

/**
 * Shows one HDMI input full screen. Amazon's own input player needs a permission only Amazon apps get, so
 * this plays the input itself with Android's TvView. Back returns to Home.
 *
 * Extras: "input" (TV input id, e.g. com.droidlogic.tvinput/.services.Hdmi1InputService/HW5), "label".
 */
public class Hdmi extends Activity {
    static final String EXTRA_INPUT = "input";
    static final String EXTRA_LABEL = "label";

    private TvView tv;
    private TextView message;
    private String label;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        tv = new TvView(this);
        message = new TextView(this);
        message.setTextColor(Color.WHITE);
        message.setTextSize(28);
        message.setGravity(Gravity.CENTER);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        root.addView(tv, new FrameLayout.LayoutParams(-1, -1));
        root.addView(message, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        tv.setCallback(new TvView.TvInputCallback() {
            @Override
            public void onVideoAvailable(String inputId) {
                message.setVisibility(View.GONE);
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }

            @Override
            public void onVideoUnavailable(String inputId, int reason) {
                if (reason == TvInputManager.VIDEO_UNAVAILABLE_REASON_TUNING
                        || reason == TvInputManager.VIDEO_UNAVAILABLE_REASON_BUFFERING) {
                    return;
                }
                showMessage("No signal on " + label);
                // Let the TV go to sleep as usual when the source is off.
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }
        });
    }

    @Override
    protected void onStart() {
        super.onStart();
        String input = getIntent().getStringExtra(EXTRA_INPUT);
        label = getIntent().getStringExtra(EXTRA_LABEL);
        if (input == null) {
            finish();
            return;
        }
        showMessage(label);
        // TvView sends no volume unless asked, and the TV's input service keeps HDMI sound muted until it gets one.
        tv.setStreamVolume(1f);
        tv.tune(input, Uri.parse("content://android.media.tv/passthrough/" + Uri.encode(input)));
    }

    @Override
    protected void onStop() {
        tv.reset();
        super.onStop();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
    }

    /** Back always leaves, even if the input would like the key (TvView passes keys to the input). */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
            if (event.getAction() == KeyEvent.ACTION_UP) {
                finish();
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    private void showMessage(String text) {
        message.setText(text);
        message.setVisibility(View.VISIBLE);
    }
}
