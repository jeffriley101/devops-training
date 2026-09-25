# R4B Android thin-shell foundation

Development shell, 2026-09-24. The repaired R4B shell has completed substantial
human validation on a Samsung SM_A166U, including the microphone repair. Final
source/build checks and the exact limits of device coverage are recorded below.
This is not a store-ready release. The FastAPI/Jinja/JavaScript application remains
Woodshed. Android Codex session: `01a0d2dd-0119-75a2-835c-a8713a434462`.

## Starting state and scope

Work started on `main` at `02a0bfd25ecf5d7df2fe8d67202aeed9ee249568` and created
`release/r4b-android-shell-20260924`. The Git repository root is the parent
`Training_scripts` directory; paths here are relative to `woodshed-woodchuck`.
There were no tracked changes. These existing untracked files were preserved:

- `docs/contest-precision-release-checklist.md`
- `docs/r4-recovery-report.md`
- `docs/r4a-artwork-integration-report.md`
- `docs/r4a-human-smoke-report.md`
- `woodshed.db.pre-analytics-20260915-130118.bak`

The agent performed no staging, commit, push, merge, deployment, production
configuration change, physical-device installation or signing-key generation.
The human subsequently installed/tested the APK, including an update with `-r`.
KHJW was read as a toolchain reference and was not modified. R4C lives separately
at `../woodshed-woodchuck-r4c/`; this audit did not read or modify that worktree.
No iOS project was created in this Android working directory.

## Project and toolchain

| Item | Value |
| --- | --- |
| Project | `mobile/android/` |
| Namespace / applicationId | `com.woodshedwoodchuck.app` (provisional) |
| Development version | `0.1.0-r4b`, versionCode `1` |
| minSdk | 26 (Android 8) |
| compileSdk / targetSdk | 36 / 36 |
| Build tools | 35.0.0, explicitly pinned |
| JDK discovered | OpenJDK 17.0.20.1, Ubuntu build `17.0.20.1+1-1-24.04-Ubuntu` |
| Gradle wrapper | 8.13, distribution SHA-256 pinned |
| Android Gradle Plugin | 8.13.2 |
| Language | Java 17; platform Android Views and WebView |
| Runtime dependencies | None outside Android/system WebView |
| Test dependencies | JUnit 4.13.2 and its Hamcrest 1.3 dependency, JVM tests only |

Confirm the package identifier **before creating the first store listing**.
No Play Console application exists as a result of this work.

The local SDK has platforms `android-36` and `android-37.0`, and build tools
35.0.0 and 36.0.0. API 37 is newer than this cached, proven toolchain supports:
[AGP 8.13 compatibility](https://developer.android.com/build/releases/agp-8-13-0-release-notes)
caps support at API 36.1 and specifies Gradle 8.13/JDK 17/build tools 35.0.0.
API 36 is therefore the highest compatible installed platform. No extra SDK
platform was downloaded. No standalone `gradle` or `sdkmanager` was on PATH.

The wrapper scripts, JAR and distribution properties were copied unchanged from
`../hoojshwah-radio/android-app/`; only this infrastructure was reused. Its
Kotlin, AndroidX and Media3 application architecture was not copied.
Wrapper JAR SHA-256:
`81a82aaea5abcc8ff68b3dfcb58b3c3c429378efd98e7433460610fecd7ae45f`.

Set `ANDROID_HOME` to your installed SDK in your own shell, or create an ignored
`mobile/android/local.properties`. No SDK paths are stored in project files.
Build artifacts, Gradle/IDE state and signing files are ignored by the Android
project's `.gitignore`. Automatic SDK downloading is disabled.

```sh
cd mobile/android
./gradlew testDebugUnitTest
./gradlew lintDebug
./gradlew assembleDebug
./gradlew assembleRelease
```

The debug build uses the machine's pre-existing standard Android debug key.
The release build is unsigned, does not read KHJW signing configuration and
does not create signing keys. The wrapper pins the distribution hash for
repeatable toolchain resolution; APK byte-for-byte reproducibility is not claimed.

## Architecture and inventory

- `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `app/build.gradle.kts`:
  one application module with no runtime Maven dependencies.
- `.gitignore`, `app/lint.xml`: local/build/signing exclusions and the narrowly
  documented target-API lint exception for the compatible local toolchain.
- `gradlew`, `gradlew.bat`, `gradle/wrapper/*`: reproducible Gradle entry points.
- `app/src/main/AndroidManifest.xml`: one exported launcher Activity; Internet,
  network-state and microphone permissions; optional microphone hardware;
  cleartext traffic and app backup disabled; no URL intent filters or services.
- `ShellConfig.java`: the single native origin setting.
- `NavigationPolicy.java`: pure strict URL classification and safe retry paths.
- `AudioPermissionPolicy.java`: pure trusted-origin/audio-only gate.
- `MicrophonePermissionFlow.java`: original-request identity, Android permission
  result and foreground handoff, with JVM state-transition tests.
- `BackPolicy.java`: named web operation and ordered Back decision.
- `MainActivity.java`: persistent product WebView, native loading/recovery,
  platform permissions, lifecycle, external Intents and system Back.
- `res/layout/activity_main.xml`, `res/values/*`, `res/values-v31/styles.xml`:
  small accessible native recovery UI and launch theme.
- `res/xml/data_extraction_rules.xml`: exclude application state from cloud
  backup and device transfer, including WebView's cookie/site storage.
- `res/mipmap-nodpi/ic_launcher.png`: one copied development icon.
- `app/src/test/.../*Test.java`: URL, microphone resource and Back decision tests.

There is no native SHED, SHOP, BOOK, BOARD, Arcade, game, authentication,
account, Classroom or Premium implementation. There is no native database,
JavaScript interface, browser engine, billing library, analytics, crash SDK,
advertising, foreground/background service, push or offline synchronization.

## Origin and WebView security

`ShellConfig.ORIGIN` is `https://woodshed-woodchuck.onrender.com`.
There is no alternate-origin build property or runtime URL input in R4B.
Incoming Intent URLs are ignored. An origin change requires a reviewed native
configuration change and binary rebuild. The origin parser requires an HTTPS
origin without credentials, path, query or fragment.

JavaScript and DOM storage are enabled because Woodshed needs them. File and
content access, file-URL cross-origin/universal access, mixed content,
geolocation, automatic popup creation and autoplay without a gesture are disabled.
Safe Browsing is enabled. Every SSL error is canceled, including in debug builds.
WebView debugging is enabled only when `BuildConfig.DEBUG` is true.
Console text is not relayed to logcat; shell debug logs contain fixed event
labels and numeric error/status codes, never URLs, IDs, cookies or auth headers.
Camera and unknown WebView permission resources are denied. No file chooser or
download/native file-access feature is implemented.

Only exact HTTPS scheme, case-insensitive host and effective port equality is
internal (`:443` equals the default HTTPS port). Lookalike hosts, trailing-dot
hosts and different ports are not trusted. Valid external HTTPS uses an Android
`ACTION_VIEW` browsable Intent; `mailto:` uses `ACTION_SENDTO`. Android chooses
the installed browser/appropriate registered handler. Missing handlers fail with
a readable message. Unknown schemes, HTTP, userinfo and malformed/ambiguous URLs
are blocked. A subframe cannot launch an external application. HTTPS subresources
remain under normal WebView web-security rules, rather than a broad native
external-origin allowlist.

Untrusted main-frame POST loads are blocked, including a request-interception
guard because WebView does not report POSTs to `shouldOverrideUrlLoading`.
The shell never replays POST bodies in an external browser. Ordinary external
HTTPS navigations and redirects are routed out. A main-frame start guard also
stops and hides unexpected untrusted loads before showing product content.

Multiple-window support is enabled solely to intercept user-initiated
`target=_blank` / `window.open` requests. A detached disposable WebView captures
the destination with JavaScript, network, file and content loading disabled.
It is never displayed and is destroyed after routing, after five seconds, or
when the Activity/page changes. A trusted URL opens in the persistent product
WebView; external HTTPS/mailto uses the same external policy. Automatic popups,
unsupported schemes and dynamic `about:blank` popup-writing workflows are blocked.
There is still only one persistent browsing WebView.

## Sessions, startup and errors

The platform `CookieManager` keeps first-party signed session cookies; third-party
cookies are disabled. Cookies are flushed after a committed page and on pause.
Startup never clears cookies or site storage. No native code reads/copies session
cookies into JavaScript, preferences, files or another authentication system.
Woodshed logout and its existing `/account/me` session boundary remain authoritative.

Cold launch always opens `/`. R4B deliberately has no disk-backed last-path
restoration or `saveState`/`restoreState` page-history snapshot. Ordinary rotation,
screen-size and multi-window layout changes keep the current WebView via manifest
configuration handling. Other Activity recreation and process relaunch start a
fresh `/` page, with the cookie store intact and the server validating the session.
OS process death can lose transient web form/game state; it does not intentionally
log the student out.

The native icon/loading state appears immediately. The WebView is revealed at a
trusted `onPageCommitVisible`; the existing web session gate controls authenticated
content. Routine in-app navigation does not flash a native loading overlay.
Main-frame network failures, unavailable pages, TLS failures, server errors and
renderer loss have readable native recovery messages and Retry. A 45-second
cold-start/Retry timeout avoids an indefinite native spinner. Network-state
inspection distinguishes apparent offline status when available. DNS, TLS and
server errors do not clear sessions or assert the student is logged out.

HTTP 401/403 responses remain web-owned so the Woodshed sign-in/access presentation
remains authoritative. Subresource/API errors, including `/account/me`, stay with
R4A's web recovery UI. Retry repeats only the intended validated same-origin
path/query, kept in memory; invalid or external retry targets fall back to `/`.
URL fragments are not retained by Retry. A dead renderer is destroyed and Retry
creates a fresh secure WebView using the same platform cookie store.

## Microphone and lifecycle

No microphone prompt occurs at launch. On a page request, both the committed
main page and requesting origin must be trusted, the Activity must be resumed
with a visible, focused window, and the resource list must contain **only**
`RESOURCE_AUDIO_CAPTURE`. Mixed
audio/video requests are denied in their entirety. If Android permission already
exists, only audio capture is granted. Otherwise one pending request waits for
the Android `RECORD_AUDIO` result. Grants are rechecked against page generation,
URL, Activity state, origin, resources and current OS permission.

Denial is passed back to the web feature without automatic prompting loops.
The existing Woodshed feature explains failure and provides any user-initiated
retry. Cancellation, navigation, a hidden window, real backgrounding, renderer
loss or destruction invalidate pending requests. During the Android permission
overlay, pause/stop notifications with a still-visible window preserve the
original request **and do not pause WebView**. The result can arrive before
resume or focus; it waits for both and is revalidated before an audio-only grant.
A canceled/invalidated request can never receive a later Android grant, and an
overlapping request cannot take ownership of the outstanding Android result.
Permission revocation is rechecked at completion and on each new request.

The shell calls WebView `onPause()`/`onResume()` and leaves R4A's existing
visibility/pagehide cleanup responsible for stopping media and capture. It does
not inject timers, auto-save BOOK, auto-restart audio, or globally pause JavaScript
timers (which could prevent cleanup). Tuner background cleanup/reopen/denial now
have Samsung device evidence below; unreported permission-dialog interruptions
and other devices still need coverage. Android's
[WebView lifecycle API](https://developer.android.com/reference/android/webkit/WebView#onPause())
is explicitly best-effort. No native audio service exists. The manifest also
declares `MODIFY_AUDIO_SETTINGS`, a normal install-time permission needed by
Chromium's recording-device selection; only `RECORD_AUDIO` is requested at runtime.

### Focused microphone repair — Samsung SM_A166U

Human-reported evidence from the original APK: installation/launch, production
loading, account/session behavior and panel-first/history Back worked. First
Tuner use displayed the Android microphone prompt. After Allow while using the
app, Tuner closed to SHED; reopening showed REQUESTING MICROPHONE and then
MICROPHONE BUSY. Force-stop/relaunch did not fix it. Android Settings showed
microphone allowed with no permissions listed as denied, while the same production
tuner worked in Chrome on that phone. This isolated the observed defect to the
native shell. **The repair is now physically validated on this Samsung for the
reported post-repair scenarios below.**

Code inspection found three native faults:

1. `onPause()` preserved the pending Java request during the permission dialog
   but unconditionally called `web.onPause()`. Chromium's
   [WebView pause implementation](https://github.com/chromium/chromium/blob/main/android_webview/java/src/org/chromium/android_webview/AwContents.java)
   updates page visibility; its
   [renderer visibility predicate](https://github.com/chromium/chromium/blob/main/android_webview/browser/gfx/browser_view_renderer.cc)
   treats a paused WebView as hidden. Existing R4A `WWLifecycle` then calls
   `stopTuner(false)`, closes the panel, increments the web session generation
   and discards any subsequently acquired stream. Retaining only the Java request
   was insufficient.
2. `onStop()` unconditionally denied/discarded the pending WebView request,
   without distinguishing a still-visible permission overlay from real backgrounding.
3. The manifest omitted `MODIFY_AUDIO_SETTINGS`.
   [Chromium's Android audio manager](https://github.com/chromium/chromium/blob/main/media/base/android/java/src/org/chromium/media/AudioManagerAndroid.java)
   checks it together with `RECORD_AUDIO` for recording-device selection.
   A successful native/WebView microphone grant therefore did not supply all
   capture prerequisites. Woodshed maps `NotReadableError` to MICROPHONE BUSY;
   that text does not prove another app holds the hardware. This manifest defect
   explains why a runtime grant and process restart alone are insufficient.
   Android documents this as a
   [normal permission](https://developer.android.com/reference/android/Manifest.permission#MODIFY_AUDIO_SETTINGS),
   separate from the user's runtime microphone consent.

These are source-confirmed defects consistent with the observed sequence, not
a captured Samsung callback trace. `adb devices` was empty during repair; the
phone's exact provider version and callback ordering have not been measured here.
No production web changes were necessary.

Repair: add the missing manifest permission and replace the scattered pending
flags with `MicrophonePermissionFlow`. Preserve the visible runtime-dialog
transition, complete only the original request after resume/focus, and recheck
origin/resources/page generation/URL/OS consent at the grant boundary. The product
WebView now observes actual window hiding so a runtime dialog never exempts real
backgrounding. Pause/stop outside that narrow transition still pause WebView and
let R4A stop capture. No stream is automatically restarted. `onUserLeaveHint` is
not used to identify backgrounding: launching a permission Activity is not a
reliable indication that the user left. Navigation/destruction/cancellation still
invalidate immediately. Security, cookies/auth, Back and web product code are unchanged.

Debug-only `WoodshedMic` logs report request receipt, Android prompt/result,
overlay preservation, deferred foreground completion, grant/deny/cancel and
invalidation reasons. They contain only fixed state labels, no URLs, accounts,
cookies or credentials. Release builds emit none of these diagnostics. For the
human retest, optional filtered logging is:

```sh
adb logcat -s WoodshedMic:D '*:S'
```

Expected first-use trace: request received → requesting Android permission →
overlay preserved (if paused) → Android callback granted → possibly waiting for
foreground → grant delivered to original request → WebView audio granted.
Already-allowed use should log Android permission already granted → WebView audio
granted, without another system dialog. A WebView grant alone is not proof that
the stream started; confirm LISTENING/pitch response on the phone.

Human post-repair results on the SM_A166U:

- **PASS:** repaired APK updated the existing installation successfully.
- **PASS:** with microphone already allowed, Tuner opened and listened, without
  a REQUESTING MICROPHONE hang or MICROPHONE BUSY state.
- **PASS:** backgrounding during active Tuner returned later to SHED; microphone
  did not silently continue/restart, and reopening Tuner worked normally.
- **PASS:** permission denial caused no crash or endless permission loop; STOP
  closed the denied/unavailable Tuner.
- **PASS:** repeated Tuner opening worked.

These results validate the repaired microphone flow on this Samsung. They do
not separately establish Pristine capture, a fresh Allow after Settings revocation,
every prompt-open interruption, or other OEM behavior. Those remain additional
coverage, not reported passes. Exact Android/WebView versions and a callback trace
were not supplied with the human results.

Focused repair file inventory (no other source changes):

- `mobile/android/app/src/main/AndroidManifest.xml`
- `mobile/android/app/src/main/java/com/woodshedwoodchuck/app/MainActivity.java`
- `mobile/android/app/src/main/java/com/woodshedwoodchuck/app/MicrophonePermissionFlow.java` (new)
- `mobile/android/app/src/test/java/com/woodshedwoodchuck/app/AudioPermissionPolicyTest.java`
- `mobile/android/app/src/test/java/com/woodshedwoodchuck/app/MicrophonePermissionFlowTest.java` (new)
- `docs/r4b-android-shell.md`

The 18 new flow tests cover already-granted capture, retained requests, native
grant/denial, exact original identity, duplicate/canceled requests, origin/resource
rejection, visible overlay pause/stop, both result/resume orderings, focus deferral,
hidden-window/background invalidation, navigation/destruction and permission
revocation. One manifest regression test checks both Chromium audio permissions
and absence of camera permission. These test native decisions; they do not emulate
a real WebView stream or claim a physical microphone pass.

Repair validation: `./gradlew --offline testDebugUnitTest lintDebug assembleDebug
assembleRelease` passed (32 tests total, lint clean, both APKs built). The version
is still `0.1.0-r4b` / code 1; identify this repair by its SHA-256:

| Repair artifact | Exact bytes | SHA-256 |
| --- | ---: | --- |
| `mobile/android/app/build/outputs/apk/debug/app-debug.apk` | 87,541 | `c4d768ea75e11b78fd2bab3123483b7751af8d407f2b411b7207c6d5163f5fb0` |
| `mobile/android/app/build/outputs/apk/release/app-release-unsigned.apk` | 64,932 | `71d8b1b642095eeccf1ce030d24a77fa1f596e828f5d28526450b7e0ea038c89` |

No APK was installed by the agent. The human subsequently installed the debug
artifact in this table and reported the passes above. Final audit rebuilding
changed the debug package bytes, as recorded below; the device-tested artifact
identity remains the 87,541-byte hash in this historical repair table.
No production web/R4C/iOS/protected files were modified, and no staging, commit,
push, merge or deployment was performed.

## Android Back

API 33+ uses platform `OnBackInvokedDispatcher`, enabled in the manifest; API
26–32 uses `onBackPressed`. Both run the same sequence:

1. On a trusted committed main page, evaluate only the fixed named operation
   `window.WWNavigation.dismissCurrent()`.
2. If it returns true, stay. This also respects the web operation's handling of
   canceled unsaved-change confirmations and busy surfaces.
3. Otherwise navigate to a trusted internal history entry, if one exists.
4. Otherwise finish the Activity normally.

Callbacks from a previous page or foreground state are discarded. Native code
does not inspect DOM, maintain a panel stack or expose a bridge. The platform
predictive-Back completion callback is supported; an interactive preview of web
panel/history transitions is not implemented. Since the web decision is
asynchronous, custom interception does not promise the platform's root-home
preview animation. Panel-first and page-history Back passed on the Samsung;
root exit and the broader predictive-gesture/API/device matrix remain unreported.

## System bars, orientation and accessibility

There is no immersive mode or orientation lock. On API 30+, a native container
applies system-bar, cutout, caption-bar and IME insets once and consumes them
before the WebView. This keeps the **web content viewport** inside the bars even
where [Android 16 enforces edge-to-edge windows](https://developer.android.com/about/versions/16/behavior-changes-16#edge-to-edge).
API 26–29 uses ordinary fitted system decor and `adjustResize`. No duplicate CSS
safe-area values are injected. Portrait → landscape → portrait passed on the
Samsung. Tablet resizing, gesture versus three-button navigation, cutouts and
keyboard/accessibility combinations still need broader coverage.

The error/status text uses a polite TalkBack live region, high contrast and `sp`
font sizing. The Retry button has a text label and a minimum 48dp target. The
native panel scrolls to accommodate large fonts and short landscape windows.
Decorative art/progress are omitted from the accessibility tree. Native code
does not explicitly move accessibility focus into the page on every navigation.

## Artwork and footprint

The sole copied image is the approved PWA
`static/img/woodshed-woodchuck-pwa-icons/icon-192.png`, copied byte-for-byte as
`app/src/main/res/mipmap-nodpi/ic_launcher.png` (42,055 source bytes).
It supplies the development launcher, the native startup view, and the Android
12+ platform splash icon. Older Android uses a solid matching launch background
before the startup view. A polished adaptive launcher treatment can be reviewed
by Artwork before store release. No new artwork was generated.

No HTML, templates, games, scenes, audio libraries, master artwork or browser
engine are in the APK. Installed system WebView supplies the engine. The only
native resources are that PNG and the small layout/string/color/theme XML files.
WebView/browser cache and downloaded site assets are **runtime storage**, separate
from APK installation payload. Cache uses the normal OS-controlled WebView mode;
no authenticated offline cache or unbounded application cache is implemented.

| Artifact | Bytes | Decimal MB | MiB |
| --- | ---: | ---: | ---: |
| Human-tested debug APK (microphone repair) | 87,541 | 0.087541 | 0.083486 |
| Final audit rebuilt debug APK | 72,347 | 0.072347 | 0.068995 |
| Unsigned release APK (microphone repair) | 64,932 | 0.064932 | 0.061924 |

Both are far below the approximate 20 MB release-shell goal. The release ZIP
contains one DEX, manifest, the PNG, compiled layout/backup rules/resource table,
and tiny build/version metadata. It contains no `assets/` payload or native
libraries. Release code is not minified, so these figures do not rely on R8
shrinking. Debug signing/alignment and debug DEX packaging account for most of
the difference. Source resources total 47,971 bytes across six files.

## Update boundary

| Normal web/Render deployment; usually no APK update | Android binary update |
| --- | --- |
| Templates, CSS, most JavaScript | Android permissions |
| Games, SHED, SHOP, BOARD, BOOK, Arcade | Native URL/navigation policy |
| Classroom and normal backend/API behavior | Startup/error UI and shell capabilities |
| Web-delivered artwork | Signing/package metadata, native icon/splash |
| Server authentication and Premium entitlements | Configured origin or native Back integration |

The user agent appends `WoodshedAndroid/0.1.0-r4b` to the normal WebView UA. It
may identify presentation preferences later; it is never authentication,
authorization, Premium proof or a security boundary. No backend behavior was
changed for the token. Web updates must continue honoring the named Back contract
and browser compatibility expected by this shell.

## Deferred work and limitations

- Package identifier confirmation, release signing/key custody, store listing,
  privacy/data declarations and other store-readiness review are future work.
- Premium membership pages stay server-authoritative web pages. External HTTPS
  payment redirects use normal external routing. Browser/WebView cookies are
  **not guaranteed to be shared**. Checkout return behavior and exact packaged-app
  purchasing presentation need review before Play Store release. There is no
  native billing or entitlement implementation.
- Permanent domain configuration precedes verified App Links. There are no
  Render-bound `autoVerify` declarations, `assetlinks.json` assumptions, custom
  schemes or native checkout-return links.
- iOS/WKWebView is deferred to R4C. No Classroom, Rhythm Baseball gameplay,
  background audio, notifications or offline-practice product work is included.
- Android 8 support does not guarantee that an obsolete installed WebView can
  run all R4A JavaScript features. Check the provider/version on modest phones.
- Upload/file chooser, downloads, camera, telephone links and dynamic popup
  writing are not shell capabilities in R4B. Unknown schemes fail safely.
- JVM tests do not prove WebView rendering, OS permission/lifecycle behavior,
  real session persistence, audio cleanup or predictive gesture behavior.
  During agent implementation no device/emulator was connected and no AVD was
  configured, so no Activity/WebView instrumentation test was run. Later human
  testing is separately documented. The Back decision is covered by pure unit tests;
  an account-free WebView fixture/instrumentation harness remains useful follow-up.
- APK size is measured locally; one Samsung installed-storage observation and
  one post-use memory snapshot are recorded below. Neither measures long-duration
  growth or low-memory pressure. Building successfully does not establish store readiness.

## Physically validated on Samsung SM_A166U

These are actual human-reported results, not inferred from unit tests. ADB
recognized the physical Samsung. The human installed the original debug build
and later successfully updated it with:

```sh
adb install -r mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

The repaired binary used for these results was **87,541 bytes**, SHA-256
`c4d768ea75e11b78fd2bab3123483b7751af8d407f2b411b7207c6d5163f5fb0`.
This audit independently verified that size/hash before running the final rebuild.
The current rebuilt APK has a different identity; see the final build record.
Device model is recorded, but no unique serial, account identifiers or raw device
logs are included. Exact Android/WebView versions were not supplied.

| Area | Human result |
| --- | --- |
| Install/startup | PASS: initial install, repaired `-r` update, launch and native startup reaching production; no crash, blank WebView or unrecoverable startup state. |
| Production/account flow | PASS: canonical Woodshed loaded; new persistent Woodchuck creation reached SHED; artwork/layout looked correct; automatic Daily XP/Streak popup appeared and could be dismissed. |
| Session/relaunch | PASS: authenticated state survived closure/relaunch. Relaunch shows the public landing page; Enter Woodshed returns to authenticated SHED without credentials. This matches browser behavior and is accepted. |
| XP focus outline | OBSERVED AND ACCEPTED: the XP hotspot retains a rectangular focus outline after popup dismissal. The tester likes its cue that the hotspot can be tapped. It is not a bug. |
| Android Back | PASS: an open SHED panel closes first while remaining in SHED; SHOP → Back returns to SHED; normal Arcade game → Back returns to Arcade. |
| Repaired microphone | PASS: already-allowed Tuner listens without REQUESTING/BUSY; backgrounding returns to SHED without continued/automatic restarted capture; reopening works. Denial has no crash/endless permission loop, STOP closes the unavailable Tuner, and repeated opening works. |
| Rotation/layout | PASS: portrait → landscape → portrait, with usable SHED/navigation/hotspots, visible artwork and no WebView crash or stuck panel. |
| External email | PASS: SHOP → Artist → Email Woodshed Support opens Android's external email handler; email is not embedded. |
| Offline/retry | PASS: connectivity disabled during use; native/web recovery handled it without crash; reconnecting and Retry restored Woodshed. |
| Arcade presentation | PASS: Arcade, bright laser background, Rhythm Baseball cabinet, Top 5/coming-soon presentation above the cabinet, and no material mobile clipping. No fake Rhythm Baseball scores/gameplay appeared. |
| Existing game/navigation | PASS: a normal existing Arcade game loaded, accepted interaction, and Back returned cleanly to Arcade. |
| Navigation endurance | PASS: SHED → BOOK → BOARD → SHOP → Arcade → normal game → Back → Arcade → SHED, with no crash, blank WebView, lost artwork, broken navigation, stuck audio or progressive functional failure. |

### Installed storage observation

Android Settings values reported after real use (preserve the displayed units):

| Category | Human-observed value |
| --- | ---: |
| App | approximately 93.70 KB |
| User data | 8.72 MB |
| Cache | 32.65 MB |
| Total | 41.47 MB |

The native payload remains tiny. Most observed storage is WebView/site cache,
separate from the APK. The displayed total slightly exceeds the earlier rough
40 MB warning line, overwhelmingly because of cache rather than bundled product
content. These are rounded device UI values, not a byte-exact storage accounting;
no claim is made that all cache is permanently required or that future growth is
bounded by this observation.

### One post-use memory snapshot

| Reported metric | Reported KB | Approximate MiB (KB / 1024) |
| --- | ---: | ---: |
| TOTAL PSS | 194111 | 189.56 (~190) |
| TOTAL RSS | 207878 | 203.01 (~203) |
| TOTAL SWAP PSS | 35746 | 34.91 (~35) |

This is one human-measured post-use snapshot, not a benchmark, guaranteed maximum,
long-duration growth study or low-memory stress test. Do not infer system-wide
WebView memory attribution or older-phone behavior from it.

### Nonblocking shared-web follow-up

During page navigation, a mostly blank/background state can briefly show small
blue links/navigation text at the bottom before artwork/page presentation is
ready. The tester accepts a blank loading moment but finds the small blue text
distracting. Future shared-web work should hide that navigation presentation
until the artwork/page is ready, benefiting Android, iOS and browser together.
**No templates, CSS, JavaScript or loading behavior were changed in this audit.**
This is not an R4B staging blocker. The accepted relaunch landing page and XP
focus rectangle must also remain unchanged.

## Still not validated / additional manual coverage

The Samsung passes are sufficient evidence for review of the R4B development
foundation, not a complete device matrix or store certification.

- [ ] Broad device matrix, substantially older Android hardware and other OEMs.
- [ ] Long-duration memory/cache growth and low-memory stress under system pressure.
- [ ] Production signing and Play Store distribution/signing.
- [ ] Packaged-app Premium checkout, browser/WebView session separation and return flow.
- [ ] Permanent-domain configuration and verified App Links (deferred, not implemented).
- [ ] Pristine microphone capture, fresh Settings revoke/Allow flow, and prompt-open
      navigation/background/rotation/screen-lock combinations not individually reported.
- [ ] Root Back exit, older-API fallback, predictive animations and both navigation modes.
- [ ] TalkBack/large-font/keyboard/cutout/tablet/multi-window combinations.
- [ ] Offline cold start, server/TLS error scenarios and missing email/browser handlers.
- [ ] External HTTPS/redirect/new-window behavior on-device; policy is source/unit validated.
- [ ] Full logout/relogin, expiry/revocation and process-kill scenarios beyond the reported
      ordinary authenticated closure/relaunch.

No unchecked scenario above is represented as a completed human test.

## Local validation record

Final combined command (with the local SDK supplied through `ANDROID_HOME`):

```sh
./gradlew --offline testDebugUnitTest lintDebug assembleDebug assembleRelease --rerun-tasks
```

Final audit result: **BUILD SUCCESSFUL**, 94 tasks executed with `--rerun-tasks`;
unit tests, lint and packaging were actually rerun, not assumed from old output.
All artifacts used the installed SDK and cached dependencies; no platform download
was needed. No Android source/configuration/resource/test file changed in this audit.

| Check | Result |
| --- | --- |
| `testDebugUnitTest` | 32 tests, 0 failures, 0 errors: URL/path 7, audio policy/manifest 4, microphone flow 18, Back policy 3 |
| `lintDebug` | Pass; `app/build/reports/lint-results-debug.txt`: `No issues found.` |
| `assembleDebug` | Pass; standard existing debug key, no new key generated |
| `assembleRelease` | Pass, intentionally unsigned; release vital lint also passed |
| `:app:dependencies --configuration releaseRuntimeClasspath` | `No dependencies` |
| `:app:dependencies --configuration debugUnitTestRuntimeClasspath` | JUnit 4.13.2 → Hamcrest Core 1.3, tests only |
| APK manifest inspection | Correct applicationId, version and SDK levels; four permissions including the microphone repair's `MODIFY_AUDIO_SETTINGS`, no camera |
| `apksigner verify` on debug APK | Pass |
| `apksigner verify` on release APK | Expected failure: no signature; confirms it is not an installable signed release |
| APK ZIP inventory | No web product, engine, audio, bundled libraries or unrelated artwork |
| Ignore checks | APK/build, `.gradle`, SDK properties, IDE state and signing paths ignored |
| Protected file hashes | All five unchanged from start |
| Git tracked/staged diffs | Empty; additions remain untracked/unstaged |
| Device evidence provenance | Agent repair-time device list was empty; later human ADB recognition/install/testing on SM_A166U is recorded separately above |

Lint warnings are treated as errors. Earlier lint findings were resolved with
API guards, compatible resources and explicit backup exclusions. Narrow
documented suppressions remain for the API 26–32 Back fallback (newer devices use
the platform dispatcher), the runtime-inset container that cannot be merged, and
target SDK 36 while using compatible AGP 8.13.2. No lint baseline was generated.

Artifact paths, relative to the Woodshed repository directory:

- `mobile/android/app/build/outputs/apk/debug/app-debug.apk`
- `mobile/android/app/build/outputs/apk/release/app-release-unsigned.apk`
- `mobile/android/app/build/reports/tests/testDebugUnitTest/index.html`
- `mobile/android/app/build/reports/lint-results-debug.html`

## Final binary identity and release audit

The final forced rebuild changed the debug output; it must not be called the
exact physically tested binary:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Human-tested debug (verified before rebuild) | 87,541 | `c4d768ea75e11b78fd2bab3123483b7751af8d407f2b411b7207c6d5163f5fb0` |
| Current `mobile/android/app/build/outputs/apk/debug/app-debug.apk` | 72,347 | `6e3f43ca19cda133d1c746a40e27650e067dcf95d124c9b3ebee3f79f78cde63` |
| Current `mobile/android/app/build/outputs/apk/release/app-release-unsigned.apk` | 64,932 | `71d8b1b642095eeccf1ce030d24a77fa1f596e828f5d28526450b7e0ea038c89` |

Every Android source/configuration/resource/wrapper/test hash matches the
pre-audit snapshot. The unsigned release APK remains byte-identical to the repair
build. Thus there was no source change to explain the debug hash change; this is
consistent with debug build/packaging variation between incremental and forced
rebuilds. The precise old-versus-new ZIP byte difference was not retained for
comparison, so no narrower mechanism is claimed. The rebuilt debug signature
verifies. The Samsung results apply to the tested hash and unchanged source;
this final debug artifact was not installed or physically retested by the agent.

### Source/security audit

| Final source check | Result |
| --- | --- |
| Canonical origin | One production setting in `ShellConfig`; HTTPS required, no runtime origin input or incoming Intent URL load. |
| URL policy | Scheme, exact case-insensitive host and effective port checked; lookalikes/different ports not internal; unsafe/ambiguous schemes blocked. |
| External navigation | External HTTPS uses browsable system Intent, mailto uses email Intent; popup router cannot execute JavaScript or load network content; main-frame guards prevent untrusted embedding. |
| TLS/native execution | SSL handlers cancel; no `proceed()`, broad JS/native bridge or arbitrary native execution API. |
| Cookies/auth | First-party cookies retained, third-party cookies disabled; no manual cookie reads/copies or native credentials. |
| WebView capabilities | File/content/file-URL universal access, mixed content and geolocation disabled; no camera permission or video grants. |
| Microphone | Exact trusted main page/request origin, audio-only resources, OS grant and foreground state required; only explicit `RESOURCE_AUDIO_CAPTURE` is granted. |
| Stale requests/lifecycle | Original request identity retained; cancellation/navigation/destruction/window hiding invalidate; old Android results cannot grant replacement requests. Visible permission overlay exemption does not bypass hidden-window cleanup. |
| Additional audio permission | `MODIFY_AUDIO_SETTINGS` is the normal permission needed for Chromium audio-device setup. It can affect audio settings, but adds no camera, background service, location or independent microphone consent; runtime `RECORD_AUDIO` remains mandatory. |
| Diagnostics/debugging | WebView debugging gated by `BuildConfig.DEBUG`; fixed-state/numeric logs contain no account/cookie/credential/query data. Both release logging methods compile to a bare return. |

These are source/static/unit checks, not claims of device-based adversarial
security testing. No release-blocking issue was found in this audit.

### Complete intended R4B file scope

Stage only the following **28 files** (27 Android project files plus this document).
Paths are relative to `woodshed-woodchuck`:

```text
docs/r4b-android-shell.md
mobile/android/.gitignore
mobile/android/app/build.gradle.kts
mobile/android/app/lint.xml
mobile/android/app/src/main/AndroidManifest.xml
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/AudioPermissionPolicy.java
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/BackPolicy.java
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/MainActivity.java
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/MicrophonePermissionFlow.java
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/NavigationPolicy.java
mobile/android/app/src/main/java/com/woodshedwoodchuck/app/ShellConfig.java
mobile/android/app/src/main/res/layout/activity_main.xml
mobile/android/app/src/main/res/mipmap-nodpi/ic_launcher.png
mobile/android/app/src/main/res/values-v31/styles.xml
mobile/android/app/src/main/res/values/strings.xml
mobile/android/app/src/main/res/values/styles.xml
mobile/android/app/src/main/res/xml/data_extraction_rules.xml
mobile/android/app/src/test/java/com/woodshedwoodchuck/app/AudioPermissionPolicyTest.java
mobile/android/app/src/test/java/com/woodshedwoodchuck/app/BackPolicyTest.java
mobile/android/app/src/test/java/com/woodshedwoodchuck/app/MicrophonePermissionFlowTest.java
mobile/android/app/src/test/java/com/woodshedwoodchuck/app/NavigationPolicyTest.java
mobile/android/build.gradle.kts
mobile/android/gradle.properties
mobile/android/gradle/wrapper/gradle-wrapper.jar
mobile/android/gradle/wrapper/gradle-wrapper.properties
mobile/android/gradlew
mobile/android/gradlew.bat
mobile/android/settings.gradle.kts
```

The actual Android filesystem enumeration examined 332 files: 27 release
candidates and 305 ignored generated/cache files (292 under build directories,
13 under `.gradle`). No symlinks were present. Every release candidate was
reviewed: no APK/AAB, local SDK properties, hardcoded SDK paths, IDE state,
keystores/signing secrets, credentials, unique device identifiers, logs, temporary
files, compiled classes or Gradle caches belong in the release list. The wrapper
JAR and approved PNG are the two intentional binary source assets. The APK ZIPs
contain no bundled web product, browser engine or native libraries.

`git check-ignore -v` confirmed build APKs, `.gradle`, `local.properties`, `.idea`,
`*.iml`, `*.jks`, `*.keystore`, `*.p12`, `*.pem`, `signing.properties` and
`keystore.properties` are excluded. No Android files are currently tracked;
there is no tracked APK hiding behind an ignore rule. Do not force-add ignored
artifacts or copy APKs outside the ignored build directory for staging.
`git diff --check`, new-text whitespace checks, XML parsing, wrapper shell syntax,
manifest inspection, dependency inspection and debug signature verification passed.

### Git state and handoff

Branch: `release/r4b-android-shell-20260924`.
HEAD: `02a0bfd25ecf5d7df2fe8d67202aeed9ee249568`.
No new branch was created. Exact final `git status --short`:

```text
?? ../woodshed-woodchuck-r4c/
?? docs/contest-precision-release-checklist.md
?? docs/r4-recovery-report.md
?? docs/r4a-artwork-integration-report.md
?? docs/r4a-human-smoke-report.md
?? docs/r4b-android-shell.md
?? mobile/
?? woodshed.db.pre-analytics-20260915-130118.bak
```

Complete protected/unrelated untracked exclusions:

- `../woodshed-woodchuck-r4c/` — separate iOS worktree; not inspected or modified.
- `docs/contest-precision-release-checklist.md`
- `docs/r4-recovery-report.md`
- `docs/r4a-artwork-integration-report.md`
- `docs/r4a-human-smoke-report.md`
- `woodshed.db.pre-analytics-20260915-130118.bak`

All five protected local-file hashes match the start of the audit. Tracked and
staged diffs are empty. Among intended release files, only this documentation
changed during the audit; Android source and production web code did not. Ignored
build artifacts were regenerated as described above. Nothing was staged, committed, pushed,
merged or deployed, and no device installation was performed by the agent.

**READY TO STAGE** for human review of the R4B development foundation. The human
should review this record, stage precisely the 28 listed files under
`mobile/android/` and `docs/r4b-android-shell.md` without force-adding generated
output, inspect the staged diff, then proceed with their commit/PR workflow.
Keep every protected/unrelated path above unstaged. A short human smoke check of
the newly rebuilt debug hash is useful before distributing that artifact; it is
not being represented as the original physically tested APK. Broad device/store
work and the blue loading-links follow-up remain deferred.
