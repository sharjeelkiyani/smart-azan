# Smart Azan TV (Android / Fire TV app)

A minimal, free, self-contained app that shows the Smart Azan TV display
(`/tv-display` on your Smart Azan server) full-screen and autoplays azan
audio when the server schedules one - no Fully Kiosk Browser, no paid
Remote Admin license, and nothing to configure beyond one address.

It works by polling the server itself (`/tv_status`) every few seconds, so
it reacts to a new azan on its own just by sitting on the page - it doesn't
need to be told "load this now" the way a Fully Kiosk-based setup does.

## Getting the APK

This repo doesn't commit a compiled APK - GitHub Actions builds one
automatically. After this folder is pushed to GitHub:

1. Go to the repo's **Actions** tab -> **Build Smart Azan TV APK**.
2. Open the latest run, scroll to **Artifacts**, and download
   `smart-azan-tv-debug-apk` (a zip containing the `.apk`).
3. If no run has happened yet, trigger one manually from that same Actions
   page ("Run workflow").

## Installing on Fire TV

1. On the Fire TV: **Settings -> My Fire TV -> Developer Options** -> turn
   on **ADB Debugging** (and **Apps from Unknown Sources** if present).
2. Note the Fire TV's IP address (**Settings -> My Fire TV -> About ->
   Network**).
3. From a computer with `adb` installed, on the same network:

   ```bash
   adb connect <fire-tv-ip>:5555
   adb install smart-azan-tv-debug.apk
   ```

4. Launch **Smart Azan TV** from the Fire TV home screen. The first time,
   it'll ask for the TV display address - **not** the `https://<pi-ip>:5050`
   address you use in a browser. Find the right one on Smart Azan's
   **Integrations** page (a separate `http://<pi-ip>:5051`-style address) -
   this is deliberate: a TV app polling for a new azan over the main site's
   self-signed HTTPS certificate can silently fail even though the page
   itself loads fine, since `fetch()` calls don't get the same
   "ignore this certificate" treatment as loading the page does. Enter it
   once and it's saved.
5. To change the address later, **press and hold Back** on the remote.

## Installing on an Android phone/tablet

Same APK. Transfer it to the device (email, cloud drive, `adb install`)
and open it - Android will ask permission to install from that source the
first time.

## Why versions are pinned, not "latest"

`build.gradle.kts` (root) and `.github/workflows/build-android-tv.yml` pin
exact versions (Android Gradle Plugin 8.5.2, Kotlin 1.9.24, Gradle 8.7,
JDK 17) instead of resolving to whatever's newest at build time. This
project may not be touched again for a long while, and "latest" drifting
out from under it is a common way old-but-working CI setups suddenly break.
If a build ever fails on a version-incompatibility error, bump the
specific version mentioned in the error rather than removing the pins.
