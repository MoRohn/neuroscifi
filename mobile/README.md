# NeuroSciFi Pocket

Pocket is the phone interface to one NeuroSciFi instance. Its field workspace runs on a phone or tablet and can learn from outcome-labelled episodes offline. The included browser client also samples the microphone for relative sound level, samples the camera for one brightness category, and accepts four self-reported activities. It serves its own static assets and calls only its paired bridge. The bridge's per-pairing predictor feeds the history screen. The main connectome's binary calibration inputs have different semantics; Pocket observations do not silently update those circuits.

## Try it on one computer

From the NeuroSciFi project root, run:

~~~sh
nsf pocket serve --db mobile-private.sqlite
~~~

Open http://localhost:8765 on that same computer. Sensors begin off. Try **Check sound** or **Mark an activity** before pairing. Copy the one-use code from the terminal to pair, then share a reviewed item. Select the learning checkbox if you want it included in the prediction. Use **Forget** on the item and refresh to see the estimate change. A terminal pairing starts a separate history. A handoff from a paired device joins its field episode group. Pairing requires the Spark process to be reachable.

## Connect an actual phone

Use a private LAN or a private VPN between the phone and Spark. Supply a TLS certificate for the hostname you will enter on the phone, its matching private key, and a certificate chain trusted by that phone. On the Spark, bind to its private interface. Substitute your actual values below:

~~~sh
nsf pocket serve --bind PRIVATE_INTERFACE_IP --port 8765 --cert /path/to/fullchain.pem --key /path/to/privkey.pem --db /path/on/encrypted-volume/mobile.sqlite
~~~

Open https://YOUR_CERTIFICATE_HOSTNAME:8765 on the phone and enter the terminal code within five minutes. One running server issues one invitation at startup. To issue another, restart the bridge against the same DB. The phone keeps its pairing credential in tab memory. Reloading loses the default in-memory credential. To resume, explicitly select “Keep pairing in this vault for 24 hours” while paired. Server-side revocation is independent. On the Spark, list pairing labels and erase abandoned pairings using the commands below. Use a private OS account, encrypted data volume, and exclude the bridge database from backups and cloud sync. The prototype stores SQLite in cleartext within that volume. It does not configure the network, certificates or encryption for you.

~~~sh
nsf pocket list-pairings --db /path/on/encrypted-volume/mobile.sqlite
nsf pocket erase-pairing LABEL --db /path/on/encrypted-volume/mobile.sqlite
~~~

## Controls and data contract

- **Sound.** A 1.5-second sampling window yields soft, moderate or strong relative to the microphone's digital full scale. Driver processing and route affect the result. Three bins have no calibrated sound-pressure meaning.
- **Light.** One local camera frame is reduced to a single pixel and classified as dim, midrange or bright. Exposure varies by phone. Neither a displayable picture nor a transcript appears in the API.
- **Activity.** Choose focus, walk, rest or other. This is self-report. No GPS, accelerometer or contact permissions are requested.
- **Speak an activity.** On a supporting browser, deliberately start local US English recognition in a private setting. Require an already installed language pack and explicit local-only mode, with no automatic download or cloud fallback. Only a whole utterance matching one of four activity labels becomes a draft. Unsupported speech, including a label followed by a name, is discarded. The user reviews the label as a self-report. The feature does not identify who spoke.
- **Review.** Only the latest local draft survives. You can discard it, save it as memory only, or separately opt it into learning. A hidden page clears the draft. Closing before sharing loses it. Manual Stop ends sensor use and preserves the displayed draft for review.
- **Listening session.** Start up to five minutes in the foreground. Review-only is the default. While the page stays visible a separate, per-session opt-in can send a coarse sound band roughly once per minute and enable learning on those events. This samples the most recent window at that point; it is not a minute-long acoustic average. Stop, hiding the page, input interruption, expiry, or a failed automatic share ends capture. A request already sent may finish. The user can inspect its receipt and forget it. No automatic retry queue. Permission prompts and input acquisition count against the session deadline.
- **Single readings.** A random v4 event ID, channel, one fixed categorical value, and a learning boolean. Unknown fields, arbitrary text and media are rejected. HTTPS and authorization headers carry transport metadata. A receiver necessarily sees a connection address and time. The prototype avoids application access logs. Infrastructure under the operator's control may keep separate logs.
- **Single-reading estimator.** For each channel, each retained opted-in observation adds one count. The predictor uses a uniform Dirichlet prior of one per category. Review the next-observation distribution via state or its highest-probability values in the UI. Selection and placement bias limit interpretation. Deleting or expiring evidence removes its contribution immediately on the next read. No weights are exported.
- **Evening review.** Shows the last time a sensor was active (kept in this tab's memory and never sent), the observation waiting for approval, and the shared observations that are changing the forecast. **Forget this** removes one item. **Forget this session** loops the existing forget calls over the IDs this tab shared since pairing, shows progress, and reports a partial failure with a count instead of hiding it. **Stop learning this pattern** calls `mute` for a channel: its existing observations stop learning, the forecast recomputes, and a new save with learning on that channel returns 423 until **Resume learning** (`unmute`). Muted channels are stored per pairing, hold no observation, and are erased with the pairing. An identical retry of an already stored event still returns 200.
- **Home screen.** `/manifest.webmanifest` and `/icon.svg` let a phone install Pocket with shortcuts to the field session, Mark an activity, and Sense. Both are in the offline shell (eight assets; the plan counted six before the manifest and icon). Chromium may want PNG icons before it offers an install prompt; the SVG is the only icon shipped.
- **Expiry.** Records expire 24 hours after receipt. A running server sweeps each minute and before reads/writes, and sweeps at startup. Duplicate delivery never extends expiry. Random ID tombstones remain per pairing to prevent replay; unpairing deletes those IDs and invalidates the credential. SQLite secure deletion is enabled. Backups and storage-device remnants are outside the demonstrated erasure scope.
- **Offline.** Once the program cache is ready, the field workspace can reopen without the Spark in browsers supporting service workers. Vault persistence also requires Web Crypto, Web Locks, storage, and a passphrase. The browser may evict data. No automatic upload queue or background browser sensing is used. Free-form dialogue and native prolonged listening remain design work.

## Use a field session

1. Before leaving connectivity, open the trusted HTTPS page and wait for **Offline shell ready**. Open a vault with a passphrase of at least 12 characters if you want to leave the page and later submit feedback. Browser origin and port must stay the same. Without a vault, hiding the page clears the session.
2. Choose your goal. Use **Check sound** or **Check sound variation**, then **Include last sound check**. Time Pocket itself, or select a screenshot or a native metrics JSON file. Inspect the source and category for each input. A picker may hide the page and clear an uncommitted draft on some platforms; import first and check the displayed context.
3. Choose **Predict next outcome** before switching tasks. After returning, unlock the vault to restore that pending estimate. Open **Why this estimate?** to inspect its distribution and evidence. Record Continued, Switched tasks or Paused. Choose learning separately. Default lifetime is 24 hours, with an optional seven-day grant for an individual completed local episode. The vault holds up to 64 entries including the unfinished prediction. Erasure or expiry can invalidate an unfinished local prediction.
4. **Share episode** sends its fixed features and provenance, UUID, outcome and learning flag. The server stores up to 64 field episodes per originating pairing. A linked-device view returns the most recent 256 in the group. Source tags on imported files are declarations and can be forged. Raw screenshots, audio, URLs, app packages, exact timestamps and sensor time series are outside the API. A reviewed export remains in Files until its owner removes it.
5. Choose **Link my phone or tablet** on a paired device. Enter its temporary code on the other device. Linked devices may read and forget shared field episodes. Enable **Consult linked devices** before a forecast to include current approved Spark episodes. Identical event IDs contribute once. This option needs connectivity, and never silently falls back when requested. Local-only episodes stay in the browser vault. Erase each copy using the corresponding local or Spark control. Revoking a pairing on Spark also removes the shared episodes it originated. The locally protected unlock credential has no effect after server revocation.

## Native collectors

**Android phones and tablets.** Open `mobile/native/android` in Android Studio with SDK 35 and the Android Gradle Plugin 8.7 compatible toolchain (Gradle 8.9 and JDK 17). Build the app module and run on API 29+. The project includes Gradle build files and uses platform Java APIs. Select system readings, usage readings, or both. Open the system's Usage Access settings to grant the app permission if needed. Optionally enter one designated app package locally. Review and save the categorical JSON, then import it in Pocket. Usage bands cover the preceding hour. App-duration and screen-duration bands count only intervals with observed endpoints; missing boundaries, split-screen and device restarts limit interpretation. The collector has no internet permission. Package identifiers and exact durations stay out of the file.

**iPhone and iPad.** In Xcode create a SwiftUI iOS app targeting iOS/iPadOS 17+ and replace its generated application entry with `mobile/native/ios/PocketMetrics.swift`. Run on the device. In Pocket Metrics, enable marks. In Shortcuts, create a personal automation for **App → Is Opened**, choose the apps yourself, and add **Mark a selected app opening**. Grant the automation its desired execution mode. Review the opening band and/or power and thermal state, export to Files, and import it in Pocket. Unmarked apps are unknown. No Screen Time entitlement is needed for this approach. Protected minute marks use a backup-excluded file; the collector prunes old marks on access, caps at 64 and attempts to erase them on disable. Locked files and missed automations reduce coverage. Only counts above zero generate a Shortcut receipt. This is an iOS app source entry point. Neither native collector was built or run on physical hardware during this edition.

**Bins.** For switch/activation events and selected Shortcut openings, few means 0–2, some 3–8 and many 9+. Durations are under 5, 5–under 20, or 20+ observed minutes. Sound variation is a local eight-second percentile spread in relative dB, split at 6 and 16 dB. Screenshot detail is the mean horizontal RGB difference in a 32-pixel-wide reduced image, split at 9 and 27 intensity units. These labels carry no semantic interpretation of the screen or the speaker.

## Verify

~~~sh
pytest -q --ignore=tests/e2e
for f in tests/js/*.mjs; do node $f; done
bash scripts/e2e.sh
~~~

The HTTP integration test builds a disposable certificate and checks a hostname-verified TLS connection. It skips if OpenSSL is unavailable. The Node checks exercise the shipped sensor controller, the service worker and the field model with synthetic media and time.

`scripts/e2e.sh` runs the browser tests in `tests/e2e` (`npm ci` installs the dev-only axe-core on first use). They drive Chromium with a fake microphone and camera against an in-process bridge on a free port and temporary database: the prototype journey, field session and vault, evening review, offline cold open, phone and tablet layouts, axe-core accessibility (no serious or critical findings), keyboard focus, and device handoff. Playwright's bundled Chromium is used by default; set `NSF_CHROMIUM=/usr/bin/chromium` to use a system browser. Screenshots of both layouts are written to `test-results/`. Native iPhone/Android capture, hardware power usage and real-world masking have not been validated.
