# PhantomShare — User Guide

> Secure file transfer between two computers over the internet.
> No registration, no installation, no network configuration required.

---

## Table of Contents

1. [What is PhantomShare](#1-what-is-phantomshare)
2. [System Requirements](#2-system-requirements)
3. [Sending a File](#3-sending-a-file)
4. [Receiving a File](#4-receiving-a-file)
5. [Connection Verification](#5-connection-verification)
6. [User Interface](#6-user-interface)
7. [Diagnostics](#7-diagnostics)
8. [Limitations](#8-limitations)
9. [Security](#9-security)
10. [Event Log (Logs)](#10-event-log-logs)
11. [Troubleshooting](#11-troubleshooting)
12. [Frequently Asked Questions (FAQ)](#12-frequently-asked-questions-faq)

---

## 1. What is PhantomShare

PhantomShare is an application for **one-time secure file transfer** between two users over the internet.

### Key Features

- 🔒 **End-to-end encryption** — your files are encrypted from start to finish. Even the relay server cannot see the content
- 🔑 **Connection verification** — visual code to confirm you're connected to the right person
- ✅ **Integrity check** — after transfer, the file is automatically verified (SHA-256)
- 📦 **Single file (.exe)** — nothing to install, just run it
- 🌐 **Works through NAT** — no public IP address or router configuration needed

### How it Works (Brief Overview)

```
Sender                         Server                          Receiver
    │                            │                                 │
    │── Connection ─────────────►│◄──────────── Connection ───────│
    │                            │                                 │
    │── Key Exchange ───────────►│── Forwards encrypted ─────────►│
    │                            │                                 │
    │── Verification ◄───────────────────────────► Verification ──│
    │                            │                                 │
    │══ Encrypted file ═════════►│══ Forwards bytes ═════════════►│
    │                            │                                 │
    │── SHA-256 ◄───────────────────────────────── SHA-256 ───────│
    │                            │                                 │
```

The server acts only as a "post office" — it forwards encrypted data without having the keys to decrypt it.

---

## 2. System Requirements

| Parameter | Minimum |
|----------|---------|
| OS | Windows 10/11 (64-bit) or Linux (64-bit) |
| Internet | Any connection (Wi-Fi, mobile, wired) |
| RAM | 100 MB |
| Disk | File size + 50 MB for the application |

> 💡 **Tip:** For Linux, there's a separate build — see [GitHub Releases](https://github.com/artmarchenko/PhantomShare/releases).
> For macOS, you can run from source code (Python 3.11+) — see [README.md](README.md).

---

## 3. Sending a File

### Step-by-Step Instructions

**Step 1.** Run `PhantomShare.exe`

**Step 2.** Make sure the **"📤 Send"** tab is selected (at the top)

**Step 3.** Click the **"📂 Browse"** button and select the file to transfer

- After selection, you'll see the file name and size
- If the file is larger than 5 GB — a yellow warning will appear (see [Limitations](#8-limitations))

**Step 4.** Click the **"📤 Send"** button

- The application will generate a **session code** — a unique code, for example: `a7f3-bc21`
- This code will appear on screen

**Step 5.** Share the session code with the recipient

- Click the **📋** button next to the code to copy it to clipboard
- Send the code to the recipient via messenger, phone, or any convenient method

**Step 6.** Wait for the recipient to connect

- Status will change to: "🟡 Waiting for partner..."

**Step 7.** Confirm verification (see [Verification](#5-connection-verification))

**Step 8.** Transfer will start automatically

- You'll see a progress bar with percentage, speed, and remaining time
- Status: "🟢 Transferring..."

**Step 9.** Wait for the completion message

- `🎉 File transferred and verified ✓` — success!
- Status will change to "🟢 Completed ✓"

---

## 4. Receiving a File

### Step-by-Step Instructions

**Step 1.** Run `PhantomShare.exe`

**Step 2.** Switch to the **"📥 Receive"** tab (at the top)

**Step 3.** Enter the **session code** given to you by the sender

- You can enter the code manually (format: `xxxx-xxxx`)
- Or click the **"📋 Paste code"** button — the code will be automatically pasted from clipboard

**Step 4.** Click **"📂 Folder"** and choose where to save the file

**Step 5.** Click the **"📥 Receive"** button

- The application will connect to the server and find the sender
- Status: "🟡 Connecting..." → "🟡 Waiting for sender..."

**Step 6.** Confirm verification (see [Verification](#5-connection-verification))

**Step 7.** The file will start downloading

- You'll see the file name, progress, speed, and remaining time

**Step 8.** Wait for completion

- `✅ Saved: filename.ext (X.X MB/s)` — file saved
- The file will appear in the selected folder

---

## 5. Connection Verification

### What is it

After the sender and receiver connect, the application shows both parties a **verification code** — an 8-character code in the format `E555-EB8B`.

This code is the same for both sides **only if** the connection is secure.

### Why is this needed

Verification protects against **man-in-the-middle (MITM) attacks** — a situation where an attacker intercepts the connection and substitutes encryption keys. If such an attack occurs, the codes will be **different**.

### What to do

1. **A dialog window appears** with a large verification code
2. **Call** or **contact** your partner and compare the codes:

   | Situation | What you see | Action |
   |----------|-----------|-----|
   | ✅ Codes match | `E555-EB8B` = `E555-EB8B` | Click **"Yes, codes match"** |
   | ❌ Codes differ | `E555-EB8B` ≠ `A123-B456` | Click **"No"** — connection will be terminated |

3. **Both** participants must confirm — if either one clicks "No", the transfer is cancelled

> **Important:** Don't ignore verification! This is the only way to ensure your connection hasn't been intercepted.

---

## 6. User Interface

### Main Window

```
┌──────────────────────────────────────────────────┐
│  🔒 PhantomShare v1.0.0           [🔍 Diag] [❓ Help]│
├──────────────────────────────────────────────────┤
│   [ 📤 Send ]    [ 📥 Receive ]                  │  ← Tabs
├──────────────────────────────────────────────────┤
│                                                  │
│  📂 Browse    file.zip (15.3 MB)                │  ← File selection
│                                                  │
│  Session code: a7f3-bc21  [📋]                  │  ← Code + Copy
│                                                  │
│  [ 📤 Send ]                                    │  ← Action button
│                                                  │
├──────────────────────────────────────────────────┤
│  ⚪ Ready                                        │  ← Status
│  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░  47.2%                │  ← Progress
│  47.2 MB / 100 MB · ⚡ 5.3 MB/s · ⏱ 0:10       │  ← Details
├──────────────────────────────────────────────────┤
│  [14:23:15] 🌐 Connecting to relay server...    │  ← Log
│  [14:23:16] 🔑 Key exchange...                  │
│  [14:23:17] 🔗 Protocol: v1 ↔ v1               │
│  [14:23:20] ✅ Verification confirmed            │
│  [14:23:21] 📦 Sending: file.zip (15.3 MB)     │
├──────────────────────────────────────────────────┤
│  [Cancel] [📋 Log] [💾 Save log]               │  ← Buttons
└──────────────────────────────────────────────────┘
```

### Interface Elements

| Element | Description |
|---------|------|
| **❓ Help** | Built-in instructions (short version) |
| **🔍 Diagnostics** | Server connection check (5 tests) + telemetry settings |
| **📂 Browse** | Select file to send |
| **📂 Folder** | Select save folder (receiver) |
| **📋 (code)** | Copy session code to clipboard |
| **📋 Paste code** | Paste code from clipboard (receiver) |
| **📤 Send / 📥 Receive** | Start transfer |
| **Cancel** | Stop transfer at any time |
| **📋 Log** | Copy entire log to clipboard |
| **💾 Save log** | Save log to text file |

### Status Indicator

Color indicator at the top of the window:

| Color | State | Meaning |
|-------|------|----------|
| ⚪ Gray | Ready | Application is waiting for user action |
| 🟡 Yellow | Connecting / Waiting | Connecting to server or waiting for partner |
| 🟠 Orange | Verification | Verification code is displayed |
| 🟢 Green | Transferring / Completed | Transfer in progress or successfully completed |
| 🔴 Red | Error | Something went wrong (see log) |

### Progress Bar

During transfer, displays:
- **Percentage** complete (47.2%)
- **Volume** of transferred data (47.2 MB / 100 MB)
- **Speed** (⚡ 5.3 MB/s)
- **Remaining time** (⏱ 0:10)

---

## 7. Diagnostics

Before the first transfer, we recommend clicking the **"🔍 Diagnostics"** button at the top of the window.

### Diagnostic Tests

The application performs 5 checks:

| # | Test | What it checks | If it fails |
|---|------|-------------|-----------------|
| 1 | 🌐 Internet | TCP connection to `1.1.1.1:443` | No internet access |
| 2 | 🔗 DNS | Resolution of server domain name | DNS problem (try `8.8.8.8`) |
| 3 | 🔒 TLS/SSL | Validity of server SSL certificate | Certificate expired or network blocking |
| 4 | ⚡ WebSocket | Full WSS connection to server | Firewall blocking WebSocket (port 443) |
| 5 | 📊 Latency | Ping delay to server | Shows response time in milliseconds |

### Results

- ✅ **Green** — test passed
- ❌ **Red** — test failed (with error description)
- If all 5 tests are green — the application is ready to work

---

## 8. Limitations

| Limitation | Details | Recommendation |
|-----------|--------|-------------|
| **Maximum 5 GB per session** | This is a server limit. After 5 GB the connection will be terminated | For larger files: split with an archiver (7-Zip, WinRAR) into parts up to 4 GB |
| **One file per session** | Protocol transfers one file | For multiple files: pack into ZIP/RAR archive |
| **Internet required** | On both devices simultaneously | Mobile internet also works |
| **Session code is one-time** | After use — invalid | For new transfer — new session |
| **Windows and Linux** | macOS not officially supported | macOS: run from source code (Python 3.11+) |

### About the 5 GB Limit

The limit is set on the server to prevent abuse. It applies to the **total volume of encrypted data** per session. Since encryption adds ~3-5% overhead:

| File size | Encrypted volume | Will pass? |
|-------------|-------------------|---------|
| 1 GB | ~1.04 GB | ✅ |
| 4.5 GB | ~4.7 GB | ✅ |
| 4.8 GB | ~5.0 GB | ⚠️ On the edge |
| 5 GB+ | >5.2 GB | ❌ Will be interrupted |

**Recommendation:** for guaranteed results, do not exceed **4.5 GB** per file.

---

## 9. Security

### What is Protected

| What | How it's protected |
|----|------------|
| **File content** | AES-256-GCM encryption (end-to-end) — server cannot see content |
| **File name and size** | Encrypted in control messages |
| **Encryption keys** | X25519 key exchange — keys are never transmitted openly |
| **Against connection substitution** | Verification code — protection against MITM attacks |
| **Against data forgery** | AES-GCM authenticates each block + SHA-256 for entire file |
| **Against interception** | WSS (TLS 1.2+) — encrypted channel to server |

### What is NOT Protected

| What | Explanation |
|----|-----------|
| **Session code** | Transmitted by you manually — protect it! Don't publish in public chats |
| **IP address** | Server sees your IP address (required for operation) |
| **Transfer fact** | Server knows a transfer occurred (but not the content) |
| **Metadata on your computer** | File is stored unencrypted on receiver's disk |

### Security Tips

1. **Always verify the verification code** — call or ask your partner to state the code
2. **Share session code via secure channel** — Signal, Telegram (secret chat), phone
3. **Don't publish session code** in public chats, forums, social networks
4. **Use the latest version** of the application — updates may contain security fixes

---

## 10. Event Log (Logs)

### Where to Find Logs

The application automatically saves logs to file:

```
%APPDATA%\PhantomShare\phantomshare.log
```

Usually this is:
```
C:\Users\<your_name>\AppData\Roaming\PhantomShare\phantomshare.log
```

### How to Get Logs from the Application

| Button | Action |
|--------|-----|
| **📋 Log** | Copies entire log from application window to clipboard |
| **💾 Save log** | Saves log to selected text file (.txt) |

### When Logs are Needed

- If transfer doesn't work — logs will help understand the cause
- If help is needed — send log to administrator

---

## 11. Troubleshooting

### General Diagnostics

Before searching for a specific problem:
1. Click **"🔍 Diagnostics"** → make sure all 5 tests are green
2. Verify both participants have internet
3. Make sure both are using the **latest version** of the application

---

### "Failed to connect to relay"

**Cause:** Application cannot connect to the server.

**Solution:**
1. Check internet — open any website in browser
2. Run diagnostics (🔍) — see which test failed
3. If DNS doesn't work — try changing DNS to `8.8.8.8` (Google) or `1.1.1.1` (Cloudflare)
4. If corporate network — firewall may be blocking WebSocket on port 443. Contact administrator
5. Try via mobile internet (hotspot from phone)

---

### "Partner waiting timeout"

**Cause:** Sender waited for receiver more than 5 minutes, or vice versa.

**Solution:**
1. Make sure both pressed the button almost simultaneously (difference — up to 5 minutes)
2. Check that session code is entered **correctly** (without extra spaces)
3. Use the **📋** button to copy and **📋 Paste code** to paste — this eliminates errors
4. Try again — click "Send" again for a new code

---

### "Invalid key exchange format" / "Key decryption error"

**Cause:** Session code entered incorrectly, or technical connection problem.

**Solution:**
1. Verify that the code **exactly** matches in both applications
2. Create a new code (click "Send" again)
3. Make sure both participants have the **same version** of the application

---

### "Incompatible partner version"

**Cause:** Partner is using an outdated version of the application.

**Solution:**
1. Both participants should download the latest version
2. Download: [phantomshare-relay.duckdns.org/download/PhantomShare.zip](https://phantomshare-relay.duckdns.org/download/PhantomShare.zip)

---

### "Hash doesn't match"

**Cause:** File was corrupted during transfer.

**Solution:**
1. Try sending the file again
2. If it repeats — check internet stability
3. Try a smaller file for testing

---

### Transfer is very slow

**Cause:** Speed is limited by the slowest connection (sender or receiver), as well as server bandwidth.

**Solution:**
1. Make sure both have stable internet
2. Avoid downloads / streaming during transfer
3. If possible — use wired connection instead of Wi-Fi
4. Normal speed: **1–10 MB/s** (depends on internet)

---

### Transfer interrupted midway

**Cause:** Internet connection loss or timeout.

**What happens automatically:**
- 🔄 Application will automatically try to **reconnect** (up to 5 attempts: 5s → 10s → 20s → 40s → 60s)
- ✅ On successful reconnection — verification is skipped automatically
- 📦 Transfer **continues from where it stopped** (not from beginning)

**If automatic reconnection failed:**
1. Application saves progress to `.resume` file (automatically)
2. Wait for stable connection
3. Sender selects **the same file** and clicks "Send" (new code)
4. Receiver enters new code
5. Transfer will continue from where it stopped (resume)
6. Progress is saved for **7 days**

---

### Windows SmartScreen blocks launch

**Cause:** Application doesn't have a digital signature from Microsoft.

**Solution:**
1. Click **"More info"**
2. Click **"Run anyway"**
3. This is safe — the application is open source

---

### Browser blocks .zip download

**Cause:** Some browsers mark downloads as suspicious.

**Solution:**
1. Chrome: click ⋮ in download bar → "Keep"
2. Edge: click "..." → "Keep anyway"
3. Firefox: usually downloads without problems

---

### Antivirus deletes or blocks .exe

**Cause:** Heuristic analysis may mark PyInstaller-packaged .exe as suspicious.

**Solution:**
1. Add `PhantomShare.exe` to antivirus exceptions
2. Or add the folder where the application is located to trusted list

---

## 12. Frequently Asked Questions (FAQ)

### Is this application secure?

**Yes.** PhantomShare uses the same encryption algorithms as Signal, WhatsApp, and other modern messengers:
- X25519 for key exchange
- AES-256-GCM for encryption
- SHA-256 for integrity verification

The server works as a "blind intermediary" — it forwards encrypted bytes without having the keys to decrypt them.

### Does the server see my files?

**No.** Encryption happens on your computer before sending. The server only sees an encrypted data stream that it cannot decrypt.

### What happens if the server is hacked?

An attacker would only get access to encrypted data. Without the encryption keys (which only the sender and receiver have), decrypting them is **impossible**.

### Is registration required?

**No.** No accounts, no passwords, no email. Just run the application and transfer your file.

### Can I transfer multiple files?

One file per session. For multiple files — pack them into one archive (ZIP, RAR, 7z) and transfer it.

### Can I transfer a file larger than 5 GB?

Server limit is 5 GB per session. For larger files:
1. Split the file into parts using 7-Zip or WinRAR (up to 4.5 GB each)
2. Transfer each part as a separate session
3. On the receiver's side — reassemble the file

### Does it work through VPN?

**Yes.** PhantomShare uses standard WebSocket over port 443 (HTTPS) — works through most VPNs and proxy servers.

### Does it work with mobile internet?

**Yes.** Any internet connection works. But for large files, stable Wi-Fi or wired connection is recommended.

### Where are received files saved?

In the folder you selected when clicking the "📂 Folder" button on the "Receive" tab. By default, this is the desktop or "Downloads" folder.

### Where to get help?

1. Click **"📋 Log"** or **"💾 Save log"** in the application
2. Send the log to administrator or open an Issue on [GitHub](https://github.com/artmarchenko/PhantomShare/issues)

---

*PhantomShare v1.0.0*
