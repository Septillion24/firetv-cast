package android.media.tv;

import android.content.Context;
import android.hardware.hdmi.HdmiDeviceInfo;

/** Compile-only stub of the Android 5+ class (the build's API jar is Android 4.1); the TV has the real one. */
public final class TvInputInfo {
    public static final int TYPE_HDMI = 1007;

    public String getId() {
        throw new RuntimeException("Stub");
    }

    public int getType() {
        throw new RuntimeException("Stub");
    }

    public HdmiDeviceInfo getHdmiDeviceInfo() {
        throw new RuntimeException("Stub");
    }

    public boolean isHidden(Context context) {
        throw new RuntimeException("Stub");
    }

    public CharSequence loadLabel(Context context) {
        throw new RuntimeException("Stub");
    }

    public CharSequence loadCustomLabel(Context context) {
        throw new RuntimeException("Stub");
    }
}
