package android.media.tv;

import java.util.List;

/** Compile-only stub of the Android 5+ class (the build's API jar is Android 4.1); the TV has the real one. */
public final class TvInputManager {
    public static final int VIDEO_UNAVAILABLE_REASON_TUNING = 1;
    public static final int VIDEO_UNAVAILABLE_REASON_BUFFERING = 3;

    public List<TvInputInfo> getTvInputList() {
        throw new RuntimeException("Stub");
    }
}
