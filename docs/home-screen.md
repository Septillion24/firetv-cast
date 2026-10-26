# TV helper app and ad-free home screen

Everything here is optional and changes settings on your TV. Read it before you run any of it, and note that
the TV-side commands change how Fire OS behaves in ways Amazon does not support. All of it can be undone, see
the end.

## What the helper app does

`tv-helper/` is a small Android app for the Fire TV, built without Android Studio:

- **Bluetooth headphones.** Fire OS has no shell command to connect, pair or disconnect Bluetooth audio, so the
  helper does it. Set `headphones_name` in `config.json` to part of your headphones' name.
- **Photo-frame home screen.** Replaces Amazon's home screen with nature photos fading into one another every
  75 seconds behind a clock, each with its place, photographer and license. Any arrow or OK press brings up a
  one-line menu (the HDMI inputs, the apps installed on the TV, Settings, and All Apps, Amazon's own app grid
  with the Appstore); Back or 15 idle seconds hide it again. HDMI plays inside the app (`Hdmi.java`), since
  Amazon's own input player only opens for Amazon apps.

Fire OS won't let another app be the home app and keeps the Home button to itself, so an accessibility service
(`HomeKeeper.java`) opens our screen whenever Amazon's comes up: Home button, power on, leaving an app. Amazon's
Settings is a page of its home screen, so when Home opens it, HomeKeeper reads the screen and steps in once it
shows anything other than Settings.

After the Home button, Android makes other apps wait 5 seconds before they can show a screen. The server keeps
`ShellHelper.java` running on the TV (started over adb, as the shell user, which may lift that wait), so our
screen comes up in under a second. Without it (TV restarted while the PC was off) it still comes, just later.
Apps can't start it themselves: Fire OS refuses adb connections from the TV to itself.

## Build and install

Needs a JDK 17 or newer (`JAVA_HOME` or PATH) and the TV switched on, since the build pulls `framework-res.apk`
from it. Everything else is downloaded into `tools/buildkit`.

```
python tv-helper/build.py
```

The first build makes a signing key, `tv-helper/keystore.p12`. **Keep it private and keep it**: it is not part of
the repository, and without it the TV refuses updates until the old helper is uninstalled. The server installs
the APK on the TV the first time you use the headphones button.

## Photos

```
pip install pillow
python tv-helper/photos.py
```

Downloads the set listed in `tv-helper/photos.json` (featured pictures from Wikimedia Commons under free
licenses, about 21 MB), fits them to 1920x1080 and copies them to the TV. To change the set, edit the list and
run it again. Without photos the home screen is plain black.

## Turn it on (on the TV, over adb)

After installing the helper, enable the service. If Home stops taking over after a reinstall, run the first
line with `""` and then again as shown, so the TV rereads the service settings.

```
adb shell settings put secure enabled_accessibility_services com.firetvcast.helper/com.firetvcast.helper.HomeKeeper
adb shell settings put secure accessibility_enabled 1
```

`adb` is in `tools/platform-tools`, and you need to `adb connect <tv address>:5555` first.

## Optional: switch off Amazon's ads and telemetry

These worked on a Hisense Fire TV in 2026; package names vary by device and Fire OS version, so check
`adb shell pm list packages amazon` first.

- Ad tracking off: `settings put secure limit_ad_tracking 1` (also `amz_limit_ad_tracking`,
  `non_amz_limit_ad_tracking`).
- Amazon packages switched off with `pm disable-user --user 0 com.amazon.<name>`: `tv.releasenotes`,
  `hybridadidservice`, `shoptv.client`, `shoptv.firetv.client`, `sneakpeek`, `tv.acr`, `media.recommendations`,
  `minitv.android.app`, `gamehub`, `tv.democontent.provider`, `storm.lightning.tutorial`, `tmm.tutorial`,
  `bueller.photos`, `bueller.music`, `tv.fw.metrics`, `wirelessmetrics.service`, `perfcollection`,
  `dp.logger`, `tv.forcedotaupdater.v2`. Fire OS refuses to switch off `ftvads.deeplinking`,
  `device.software.ota` and `device.software.ota.override`, so system updates still arrive.

Repeat all of this after a factory reset.

## Undo

- `adb shell settings delete secure enabled_accessibility_services` brings Amazon's home screen back.
- `adb shell pm enable com.amazon.<name>` turns a package back on.
- `adb uninstall com.firetvcast.helper` removes the helper.
