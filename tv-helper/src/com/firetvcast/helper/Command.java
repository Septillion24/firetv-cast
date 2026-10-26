package com.firetvcast.helper;

import android.bluetooth.BluetoothA2dp;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothProfile;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;

import org.json.JSONArray;
import org.json.JSONObject;

import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Bluetooth headphone control for the PC, which has no shell command for it on Fire OS.
 *
 * Run from adb:  am broadcast -n com.firetvcast.helper/.Command -f 0x20 --es cmd connect --es name "My Headphones"
 * Commands: status, connect, disconnect, pair (searches for headphones in pairing mode, pairs, then connects),
 * scan (lists every Bluetooth device nearby).
 * The answer is a JSON object in the broadcast's result data. Only callers holding the DUMP
 * permission (the adb shell) may send it.
 */
public class Command extends BroadcastReceiver {
    private static final long CONNECT_TIMEOUT_MS = 10000;
    private static final long SCAN_TIMEOUT_MS = 30000;
    private static final long LIST_SCAN_MS = 15000;
    private static final long BOND_TIMEOUT_MS = 15000;

    @Override
    public void onReceive(Context context, Intent intent) {
        final PendingResult result = goAsync();
        final Context app = context.getApplicationContext();
        final String cmd = intent.getStringExtra("cmd");
        final String name = intent.getStringExtra("name");
        new Thread(new Runnable() {
            @Override
            public void run() {
                JSONObject answer = new JSONObject();
                try {
                    answer = execute(app, cmd == null ? "status" : cmd, name == null ? "" : name);
                } catch (Throwable error) {
                    put(answer, "ok", false);
                    put(answer, "error", String.valueOf(error));
                }
                result.setResultCode(0);
                result.setResultData(answer.toString());
                result.finish();
            }
        }).start();
    }

    private static JSONObject execute(Context app, String cmd, String name) throws Exception {
        BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
        JSONObject answer = new JSONObject();
        if (adapter == null || !adapter.isEnabled()) {
            put(answer, "ok", false);
            put(answer, "error", "bluetooth_off");
            return answer;
        }
        BluetoothA2dp a2dp = a2dpProxy(app, adapter);
        try {
            if (cmd.equals("pair")) {
                BluetoothDevice device = find(adapter, name);
                Map<String, String> seen = new HashMap<String, String>();
                if (device == null) {
                    device = scan(app, adapter, name, SCAN_TIMEOUT_MS, seen);
                    if (device != null && bond(device) == null) {
                        put(answer, "ok", false);
                        put(answer, "error", "pair_failed");
                        return answer;
                    }
                }
                if (device == null) {
                    put(answer, "ok", false);
                    put(answer, "error", "not_found");
                    answer.put("seen", new JSONObject(seen));
                    return answer;
                }
                return connect(a2dp, device);
            }
            if (cmd.equals("scan")) {
                Map<String, String> seen = new HashMap<String, String>();
                scan(app, adapter, "", LIST_SCAN_MS, seen);
                put(answer, "ok", true);
                answer.put("seen", new JSONObject(seen));
                return answer;
            }
            if (cmd.equals("connect") || cmd.equals("disconnect")) {
                BluetoothDevice device = find(adapter, name);
                if (device == null) {
                    put(answer, "ok", false);
                    put(answer, "error", "not_paired");
                    return answer;
                }
                return cmd.equals("connect") ? connect(a2dp, device) : disconnect(a2dp, device);
            }
            put(answer, "ok", true);
            JSONArray devices = new JSONArray();
            for (BluetoothDevice device : adapter.getBondedDevices()) {
                JSONObject entry = new JSONObject();
                entry.put("name", device.getName());
                entry.put("address", device.getAddress());
                entry.put("audio", isAudio(device));
                entry.put("connected", a2dp.getConnectionState(device) == BluetoothProfile.STATE_CONNECTED);
                devices.put(entry);
            }
            answer.put("devices", devices);
            return answer;
        } finally {
            adapter.closeProfileProxy(BluetoothProfile.A2DP, a2dp);
        }
    }

    private static BluetoothA2dp a2dpProxy(Context app, BluetoothAdapter adapter) throws Exception {
        final BluetoothA2dp[] proxy = new BluetoothA2dp[1];
        final CountDownLatch ready = new CountDownLatch(1);
        adapter.getProfileProxy(app, new BluetoothProfile.ServiceListener() {
            @Override
            public void onServiceConnected(int profile, BluetoothProfile service) {
                proxy[0] = (BluetoothA2dp) service;
                ready.countDown();
            }

            @Override
            public void onServiceDisconnected(int profile) {
            }
        }, BluetoothProfile.A2DP);
        if (!ready.await(5, TimeUnit.SECONDS)) {
            throw new IllegalStateException("a2dp_unavailable");
        }
        return proxy[0];
    }

    private static BluetoothDevice find(BluetoothAdapter adapter, String name) {
        for (BluetoothDevice device : adapter.getBondedDevices()) {
            if (matches(device.getName(), name)) {
                return device;
            }
        }
        return null;
    }

    private static boolean matches(String deviceName, String wanted) {
        return deviceName != null && !wanted.isEmpty()
                && deviceName.toLowerCase().contains(wanted.toLowerCase());
    }

    private static boolean isAudio(BluetoothDevice device) {
        return device.getBluetoothClass() != null
                && device.getBluetoothClass().getMajorDeviceClass() == 0x0400;
    }

    /** Searches until a device whose name matches `name` shows up (an empty name lists everything seen). */
    private static BluetoothDevice scan(Context app, final BluetoothAdapter adapter, final String name,
                                        long timeoutMs, final Map<String, String> seenNames) throws Exception {
        final BluetoothDevice[] found = new BluetoothDevice[1];
        final CountDownLatch done = new CountDownLatch(1);
        final long end = System.currentTimeMillis() + timeoutMs;
        BroadcastReceiver listener = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (BluetoothAdapter.ACTION_DISCOVERY_FINISHED.equals(intent.getAction())) {
                    if (found[0] == null && System.currentTimeMillis() < end) {
                        adapter.startDiscovery();
                    }
                    return;
                }
                BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                if (device == null) {
                    return;
                }
                String deviceName = intent.getStringExtra(BluetoothDevice.EXTRA_NAME);
                if (deviceName == null) {
                    deviceName = device.getName();
                }
                seenNames.put(device.getAddress(), deviceName == null ? "" : deviceName);
                if (matches(deviceName, name)) {
                    found[0] = device;
                    done.countDown();
                }
            }
        };
        IntentFilter filter = new IntentFilter(BluetoothDevice.ACTION_FOUND);
        filter.addAction(BluetoothDevice.ACTION_NAME_CHANGED);
        filter.addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED);
        app.registerReceiver(listener, filter);
        try {
            adapter.cancelDiscovery();
            adapter.startDiscovery();
            done.await(timeoutMs, TimeUnit.MILLISECONDS);
        } finally {
            app.unregisterReceiver(listener);
            adapter.cancelDiscovery();
        }
        return found[0];
    }

    private static BluetoothDevice bond(BluetoothDevice device) throws Exception {
        if (device.getBondState() != BluetoothDevice.BOND_BONDED) {
            BluetoothDevice.class.getMethod("createBond").invoke(device);
            long end = System.currentTimeMillis() + BOND_TIMEOUT_MS;
            while (device.getBondState() != BluetoothDevice.BOND_BONDED && System.currentTimeMillis() < end) {
                Thread.sleep(250);
            }
        }
        return device.getBondState() == BluetoothDevice.BOND_BONDED ? device : null;
    }

    private static JSONObject connect(BluetoothA2dp a2dp, BluetoothDevice device) throws Exception {
        call(a2dp, "setPriority", device, 100);
        call(a2dp, "connect", device);
        long end = System.currentTimeMillis() + CONNECT_TIMEOUT_MS;
        while (a2dp.getConnectionState(device) != BluetoothProfile.STATE_CONNECTED
                && System.currentTimeMillis() < end) {
            Thread.sleep(200);
        }
        call(a2dp, "setActiveDevice", device);
        JSONObject answer = new JSONObject();
        boolean connected = a2dp.getConnectionState(device) == BluetoothProfile.STATE_CONNECTED;
        answer.put("ok", connected);
        answer.put("name", device.getName());
        if (!connected) {
            answer.put("error", "no_answer");
        }
        return answer;
    }

    private static JSONObject disconnect(BluetoothA2dp a2dp, BluetoothDevice device) throws Exception {
        call(a2dp, "disconnect", device);
        long end = System.currentTimeMillis() + CONNECT_TIMEOUT_MS;
        while (a2dp.getConnectionState(device) != BluetoothProfile.STATE_DISCONNECTED
                && System.currentTimeMillis() < end) {
            Thread.sleep(200);
        }
        JSONObject answer = new JSONObject();
        answer.put("ok", a2dp.getConnectionState(device) == BluetoothProfile.STATE_DISCONNECTED);
        answer.put("name", device.getName());
        return answer;
    }

    /** Hidden BluetoothA2dp methods; Android 9 still allows apps to reach them. Missing ones are skipped. */
    private static void call(BluetoothA2dp a2dp, String method, BluetoothDevice device, Object... extra) {
        try {
            if (extra.length == 0) {
                Method m = BluetoothA2dp.class.getMethod(method, BluetoothDevice.class);
                m.invoke(a2dp, device);
            } else {
                Method m = BluetoothA2dp.class.getMethod(method, BluetoothDevice.class, int.class);
                m.invoke(a2dp, device, extra[0]);
            }
        } catch (ReflectiveOperationException ignored) {
        }
    }

    private static void put(JSONObject object, String key, Object value) {
        try {
            object.put(key, value);
        } catch (Exception ignored) {
        }
    }
}
