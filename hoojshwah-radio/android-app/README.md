# KHJW Android

The official KHJW Android client uses one Media3 `ExoPlayer` owned by a
`MediaSessionService`. The Activity connects through `MediaController`; it does
not own playback. The existing KHJW site remains embedded at
`https://hoojshwah-radio-live.onrender.com/?app=android` for interactive station
features, with browser audio disabled in that mode.

## Build

Requirements: JDK 17 and Android SDK Platform 36/build-tools.

```sh
./gradlew assembleDebug
```

The debug APK is generated at `app/build/outputs/apk/debug/app-debug.apk`.

## Device acceptance test

Install the debug APK, open KHJW, grant notifications, and tap the page's Play
Signal button. Lock the phone before the current track ends and leave it locked through
at least two track changes. Confirm uninterrupted audio plus working notification,
lock-screen, and Bluetooth/headset Play/Pause controls. No screen wake lock is
used; the display should remain off.
