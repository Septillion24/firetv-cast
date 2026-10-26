package com.firetvcast.helper;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * A tiny process the PC starts on the TV as the shell user, for the one thing HomeKeeper can't do itself:
 * after the Home button, Android holds back other apps' screens for 5 seconds, and only holders of
 * STOP_APP_SWITCHES (the shell, not apps) can lift that. HomeKeeper asks over a loopback TCP port right
 * before it opens Home. Nothing else is offered, and only the helper app's own user id is served (SELinux
 * keeps apps off the shell's Unix sockets, so the caller is looked up in the kernel's TCP table instead).
 *
 * Started from adb (the PC does this when it connects; it lasts until the TV restarts):
 *   CLASSPATH=<path of the helper's apk> app_process /system/bin com.firetvcast.helper.ShellHelper <app uid>
 */
public final class ShellHelper {
    static final int PORT = 58421;

    private ShellHelper() {
    }

    public static void main(String[] args) throws Exception {
        int allowedUid = Integer.parseInt(args[0]);
        ServerSocket server;
        try {
            server = new ServerSocket(PORT, 4, InetAddress.getByName("127.0.0.1"));
        } catch (IOException alreadyRunning) {
            return;
        }
        Object activityManager = Class.forName("android.app.ActivityManager").getMethod("getService").invoke(null);
        while (true) {
            Socket client = server.accept();
            try {
                if (ownerUid(client.getPort()) == allowedUid && client.getInputStream().read() >= 0) {
                    activityManager.getClass().getMethod("resumeAppSwitches").invoke(activityManager);
                    client.getOutputStream().write(1);
                }
            } catch (Exception ignored) {
                // A broken request only affects that caller.
            } finally {
                client.close();
            }
        }
    }

    /** The user id owning the connection from `clientPort` to our port, from /proc/net/tcp(6); -1 if unknown. */
    private static int ownerUid(int clientPort) throws IOException {
        String local = String.format(":%04X", clientPort);
        String remote = String.format(":%04X", PORT);
        for (String table : new String[] {"/proc/net/tcp6", "/proc/net/tcp"}) {
            BufferedReader reader = new BufferedReader(new FileReader(table));
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] fields = line.trim().split("\\s+");
                    if (fields.length > 7 && fields[1].endsWith(local) && fields[2].endsWith(remote)) {
                        return Integer.parseInt(fields[7]);
                    }
                }
            } finally {
                reader.close();
            }
        }
        return -1;
    }
}
