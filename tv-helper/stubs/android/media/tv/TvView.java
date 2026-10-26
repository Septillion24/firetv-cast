package android.media.tv;

import android.content.Context;
import android.net.Uri;
import android.view.ViewGroup;

/** Compile-only stub of the Android 5+ class (the build's API jar is Android 4.1); the TV has the real one. */
public class TvView extends ViewGroup {
    public TvView(Context context) {
        super(context);
    }

    public void tune(String inputId, Uri channelUri) {
        throw new RuntimeException("Stub");
    }

    public void reset() {
        throw new RuntimeException("Stub");
    }

    public void setStreamVolume(float volume) {
        throw new RuntimeException("Stub");
    }

    public void setCallback(TvInputCallback callback) {
        throw new RuntimeException("Stub");
    }

    @Override
    protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
    }

    public abstract static class TvInputCallback {
        public void onVideoAvailable(String inputId) {
        }

        public void onVideoUnavailable(String inputId, int reason) {
        }
    }
}
