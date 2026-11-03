# SMS_connect Bridge: Android SMS to administrator WhatsApp


## What has been delivered

An Android app project plus a Python server. The owner explicitly enables access. Only SMS matching a configured sender allowlist AND at least one keyword are queued. The server rechecks those filters, maps that device to its administrator, and sends an approved WhatsApp template through Meta's Cloud API.

New incoming SMS can be checked automatically. An optional button scans up to 500 inbox messages from the last seven days. Internet is required for submission, but pending work is retained by Android WorkManager. The server uses a persistent event key to suppress duplicate submissions. WhatsApp access tokens stay on the server. Device tokens and queued SMS content are encrypted using Android Keystore.

This is a separate phone-to-server bridge. It has NOT been deployed or connected to the existing FineFlow website. The website's browser records remain separate.

## Current validation and limits

* Eleven Python tests passed, including an actual local HTTP request, unauthorized requests, authenticated webhook signatures, duplicate requests in parallel, filters and routing.
* The Android project has NOT been compiled or installed. Android SDK and Gradle are unavailable in the creation environment. An APK is NOT included.
* Meta delivery has NOT been tested. No account credentials or real administrator number were supplied.
* This is a pilot, not a Play Store approved application. SMS permissions are restricted by Google Play. A distribution eligibility review is required before public release. For an authorized controlled pilot, a debug APK can be installed directly on test devices.
* The server does not infer whether a fine is genuine, assign drivers, collect payment or record administrator acknowledgment. WhatsApp delivery/read status is supported through signed webhooks. A read receipt is not acknowledgment.
* An allowed sender name can be spoofed. Use the notice as a prompt for official verification, not as proof to pay.
* A dedicated Android phone is recommended for the pilot. Ordinary iPhone inbox access in Ghana is not supported by this project.

## 1. Run the server in test mode

Python 3.11 or newer is required. No Python packages need to be installed.

From the extracted project directory:

```bash
python -m unittest discover -s tests -v
python server/bridge.py add-device \
  --device owner-001 \
  --owner 'Owner test account' \
  --admin 233200000000 \
  --sender TEST-FINES \
  --keyword fine \
  --keyword penalty \
  --admin-opt-in
python server/bridge.py serve
```

The phone number above is an illustrative placeholder. Replace it with the consenting administrator's actual WhatsApp number, country code included, digits only. `--admin-opt-in` records that consent has already been obtained. Do not use it before obtaining consent.

The command prints a device token once. Transfer it privately to the owner and enter it in the app. Each owner phone must have a separate device record and token. The administrator destination is set on the server, never supplied by an SMS. Removing a device from `devices.json` revokes future authenticated submissions. The server reloads this file for every request.

The server listens only on 127.0.0.1:8080. For phone access, run it on an internet-accessible host behind an HTTPS reverse proxy. Example Caddy configuration, after pointing a real domain to your host:

```text
bridge.your-domain.example {
    request_body {
        max_size 16KB
    }
    reverse_proxy 127.0.0.1:8080
}
```

Replace the example domain. Configure a service supervisor to restart Python, set a private working directory, restrict configuration/database permissions, use encrypted host storage, and restrict access to the host. Apply request-rate limits at the gateway. The included threaded Python HTTP server is a small controlled-pilot component, not a hardened high-volume public API. Back up the database privately if continuity is required.

Default mode is `test`. In this mode a matching event receives `simulated`, and NO WhatsApp message is sent.

## 2. Build and install the Android app

Option A, Android Studio:

1. Open the `android` folder as a project.
2. Use JDK 17, Android SDK 35, and Gradle 8.11.1. Install missing components when prompted.
3. Sync Gradle, then build a debug APK with Build > Build APK(s), or run `gradle assembleDebug` from `android` when Gradle is installed.
4. The expected output is `android/app/build/outputs/apk/debug/app-debug.apk`.
5. Install it on an authorized Android 8.0 or newer test phone, for example `adb install app-debug.apk`.

Option B, GitHub build:

Upload this project to a private repository, preserving `.github/workflows/android.yml`. Open Actions > Build Android pilot > Run workflow. Download the APK artifact after a successful build. This workflow is supplied but has not been run here. Public action references should be reviewed and pinned to approved commit SHAs for a managed release.

A debug APK is for pilot testing. A signed release, managed updates and public-distribution permission eligibility remain separate release steps.

## 3. Configure the owner phone

1. Open FineFlow Bridge.
2. Enter the HTTPS server origin, such as `https://bridge.your-domain.example`, without a path.
3. Enter the device token from server setup.
4. Enter allowed SMS senders. Use the exact sender ID/address shown in the owner's Messages app. Do not guess official sender names. Mirror the allowlist in the server configuration.
5. Enter keywords, such as `fine,penalty,offence,offense`. Both phone and server filters must permit the notice.
6. Read and select the owner authorization/consent statement, then press Save and enable forwarding.
7. Press Request access for NEW SMS, then allow the permission.
8. For earlier messages, press Scan matching SMS from the last 7 days. Grant inbox permission, then press Scan again.
9. Press Refresh status to see the most recent local result. Run the server status command for authoritative delivery status.

The owner can stop forwarding and cancel pending work at any time. A request already sent to the server cannot be recalled. Do not force-stop the app if background forwarding is needed. Review device battery restrictions during the pilot. Permission revocation, phone shutdown and Android restrictions can delay or stop capture.

Use a test sender/address controlled by the team. For emulator testing, configure the phone and server sender allowlist to `15551234567`, then run:

```bash
adb emu sms send 15551234567 "Fine notice TEST-001 for vehicle TEST-001. Do not pay."
```

Emulator access to an HTTPS server still needs to be configured. For a real phone, send an explicitly labeled test SMS from a team-controlled number matching the filters.

## 4. Activate live WhatsApp delivery

Only after test routing and the Android build pass:

1. Set up a Meta developer app, WhatsApp Business Account and registered WhatsApp business sender number.
2. Obtain the access token, business phone-number ID and app secret. Store them on the server, not in the Android app, source repository or a chat message.
3. Obtain and record the administrator's consent to receive these notifications.
4. Submit a utility template named `fineflow_notice`, or update `WA_TEMPLATE` to the approved name. Category and approval are Meta's decision. Use four BODY text parameters, in the exact order shown below. Confirm the approved language matches `WA_LANGUAGE`.
5. Copy `server/env.example` to a private file outside the shared source directory. Fill in absolute configuration/database paths and your credentials. Choose a currently supported Graph API version from Meta's dashboard. Set `FINEFLOW_MODE=live` only when ready.
6. Source the private environment file in your service startup and restart the server.
7. Set Meta's HTTPS webhook callback to `https://YOUR-HOST/webhook`. Set its verify token to the same `WA_VERIFY_TOKEN`, and subscribe to WhatsApp message events. `WA_APP_SECRET` is used to verify POST signatures.
8. Send one explicitly labeled pilot notice to the owner phone and confirm it reaches the correct administrator.

Suggested template BODY for submission:

```text
A vehicle notice was received for {{1}}.
SMS sender: {{2}}
Notice: {{3}}
FineFlow reference: {{4}}
Please review the notice through the official channel before taking action.
```

Body parameters: owner label, original SMS sender, normalized original notice text, event reference. The notice text is whitespace-normalized for template compatibility. Template approval for this content is not guaranteed. Adjust the approved text and code together if Meta requires a different structure.

The business account is the WhatsApp sender. The owner's personal WhatsApp account is not read or impersonated. A business-initiated notice is always sent using a template by this implementation.

## 5. Check outcomes and retention

```bash
python server/bridge.py status
python server/bridge.py purge --days 30
```

Status output contains device ID, event ID, status and update time, without the SMS body. Protect host access. Schedule the retention purge according to the agreed retention policy. Purging an event also removes its duplicate-prevention record. Scans cover only seven days, so keep retention longer than seven days unless that tradeoff is intentional. Secure deletion is enabled for the purge, but it does not erase previous filesystem backups or storage snapshots.

| Status | Meaning |
| --- | --- |
| filtered | Sender or keyword did not match. Body was not stored. |
| simulated | Test mode, no WhatsApp delivery attempted. |
| processing | Reserved for sending. A server crash may leave this state. Review manually. |
| configuration_error | Missing credentials or recipient setup. No automatic resend. |
| accepted | Meta accepted the API request. Delivery is not confirmed. |
| sent / delivered / read | Reported by a signed Meta webhook. Read may be unavailable. |
| rejected / failed | Submission rejected or delivery failed. Review manually. |
| uncertain | Outcome unknown, for example a network timeout after submission. Review before resending. |

Phone-to-server requests retry on network and server errors. The server never automatically resends an event already reserved. This reduces duplicate WhatsApp messages but means interrupted, rejected or uncertain events need manual review. A deliberately authorized retry can be submitted with a new event ID after checking provider history. Do not change device IDs or purge records to force retries.

If server routing is changed, already queued phone work retains the prior URL/token but the server uses the CURRENT administrator mapping when it arrives. Cancel pending work before transferring a phone or changing its administrator. New incoming SMS after the change should be tested.

## Before operational use

Complete an Android build and physical-device test, confirm actual notice sender IDs and sample formats, obtain template approval, configure hosting and secrets, and verify real webhook delivery. Add business monitoring, reconciliation and administrative access controls before expanding beyond a controlled pilot. This project has no official affiliation with DVLA, MTTD or Ghana Police.

## References used

* Android SMS permissions: https://developer.android.com/reference/android/Manifest.permission
* Google Play SMS permission policy: https://support.google.com/googleplay/android-developer/answer/10208820
* Android broadcasts: https://developer.android.com/develop/background-work/background-tasks/broadcasts
* Meta's WhatsApp API collection: https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api
* Meta's template SDK reference: https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/messages/template/
