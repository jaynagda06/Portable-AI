# Security model, privacy, and Safe Exam Browser testing

This page explains what the app does to protect itself, what it cannot protect, and the legitimate
scope for analyzing exam/proctoring software such as Safe Exam Browser (SEB).

---

## 1. How this app is exposed

| Surface | Default | Notes |
|---|---|---|
| Web UI | `127.0.0.1:8099` | Loopback only unless LAN mode is enabled |
| Model API | random `127.0.0.1` port | Chosen at each start; not reachable from the LAN |
| Embedding API | random `127.0.0.1` port | Same |
| Outbound network | none | No telemetry, CDN, fonts, update checks or cloud calls |
| LAN mode | off | `0.0.0.0:8099` + token, only when explicitly enabled |

### Controls that are implemented

- **Loopback by default.** Normal mode binds `127.0.0.1`, so other machines cannot connect.
- **Host header check.** In local mode, requests whose `Host` is not `127.0.0.1`, `localhost` or
  `::1` get HTTP 403, which blunts DNS-rebinding attacks from a malicious web page.
- **CSRF header.** Every mutating API call must carry `X-Requested-With: PortableAI`, a header a
  cross-origin HTML form cannot set.
- **LAN token.** LAN mode requires a 32-character random token (constant-time compare). It can be
  supplied as `?t=`, an `X-Auth-Token` header, or an HttpOnly `SameSite=Lax` cookie set by the
  first tokenized page load.
- **Random model ports + CORS allow-list.** The unauthenticated llama.cpp server listens on a
  random loopback port and only accepts the app's own origin, limiting drive-by use by local web
  pages. Note: llama.cpp b11871 has no API-key option, so any *local process* can still reach it.
- **Filename and route sanitization.** Upload names are reduced to a safe character set; static
  file serving only accepts `[A-Za-z0-9_.-]`; report IDs must match 16 hex characters.
- **Upload handling.** Binary uploads stream to `data\uploads\`, are analyzed statically, and the
  temporary file is deleted in a `finally` block. The app never executes a sample.
- **No HTML injection from reports.** Report fields are escaped before rendering; Markdown answers
  are rendered by a small local renderer that escapes HTML first.

### Honest limitations

- **No TLS.** LAN traffic is plain HTTP. Use it on a trusted home network, never over the internet
  or public Wi-Fi.
- **One shared token, no user accounts.** Anyone with the token URL can use the app while it runs.
  Disable LAN mode when finished.
- **No rate limiting.** A local or LAN client can send requests as fast as the model allows.
- **Prompt injection is possible.** Documents and binaries are untrusted input. Text inside a PDF or
  a string table can try to instruct the model. Do not treat model output as a security decision,
  and do not index documents containing secrets you would not paste into a chat.
- **Data is stored in plain files.** Reports, the library DB and logs are unencrypted. Use
  BitLocker/VeraCrypt for sensitive material, and wipe the drive before disposing of it.
- **The model can be wrong.** It is a 4B local model. It summarizes evidence; it is not an
  antivirus, sandbox, or source of truth.

### Privacy

- Chat history is kept in the browser's `localStorage` for that profile only.
- The server stores: analyzed reports, indexed document text/embeddings, settings, logs.
- Nothing is uploaded anywhere. You can verify this by watching the process with a network monitor
  while the app runs, or by running it with networking disabled after startup.
- Delete `data\` to remove all reports and library content; clear site data in the browser to remove
  chat history.

---

## 2. Safe Exam Browser (SEB): scope

SEB is a locked-down browser used by schools and certification providers to deliver exams. It
restricts navigation, switching applications, shortcuts and screen capture, and it may integrate
with a proctoring service.

**This project does not help defeat, bypass, disable, or evade SEB or any proctoring system.** That
includes killing or hiding processes, spoofing the SEB environment, injecting into it, extracting
exam content, blocking detection, or feeding answers from another device. Those requests will be
refused.

That boundary is not a comment on curiosity. It is about consent and authorization: circumventing
an exam's controls harms the institution, other candidates, and often violates law and academic
policy, regardless of intent.

### Legitimate work that *is* in scope

1. **Verifying your own exam setup.** Use the official SEB Config Tool to inspect a configuration
   file you are responsible for (start URL, allowed applications, quit password, browser exam key)
   and confirm it matches your institution's instructions.
2. **Accessibility accommodations.** If SEB's restrictions conflict with a documented need, work
   with the exam administrator or disability services. They can issue an accommodated config. Do
   not modify the installed browser yourself.
3. **Authorized compatibility testing.** In a lab you control, with written permission, verify that
   an SEB version launches with your exam platform, that the required helper applications are
   allowed, and that the network/proxy configuration works. Test *your* deployment, not someone
   else's live exam.
4. **Static inspection of an authorized copy.** Hashing and identifying the version you were given,
   reading its manifest/resources/strings to understand dependencies or a crash, or checking a
   signature — using a copy you are licensed to have. This app's Binary Lab can produce the hash,
   version info, imports, resources and strings; it never runs the file.
5. **Incident response after a failure.** If SEB crashes or blocks a legitimate resource during a
   practice run, collect its logs and version, then give them to your support contact. Do not patch
   around it during an exam.
6. **Policy and awareness work.** Explaining how lockdown browsers work at a conceptual level,
   writing test plans, or training staff — without operational bypass instructions.

### Explicitly out of scope

- defeating kiosk/lockdown restrictions, disabling monitoring, or evading proctoring detection
- extracting exam questions/answers, screen-scraping a protected session, or remote answer-feeding
- tampering with SEB binaries, configuration signatures, or browser exam keys
- analyzing a proctoring vendor's client to find and exploit weaknesses against a live exam
- using this app (or any local model) to generate bypass steps, even "for research"

If you believe a proctoring product has a security flaw, report it through the vendor's security
contact or a coordinated-disclosure program. Testing a live exam without authorization is not
research.

### A responsible lab checklist

- Written permission that names the software, version, scope and dates.
- An isolated VM or spare machine with snapshots; no real exam session, no real candidate data.
- A copy of the installer/config you are authorized to possess, with its hash recorded.
- A test plan limited to compatibility, accessibility and crash diagnosis.
- Agreed handling for any sensitive data encountered; delete it when finished.
- A disclosure path for findings, and no publication before the vendor responds.

### If you are a student reading this

If SEB is blocking something you legitimately need, the durable fix is an accommodation or a
corrected exam configuration from the people running the exam. Bypassing the browser may invalidate
your result and can lead to disciplinary action. The same applies to "AI assistant" tools: using one
during a closed-book assessment is misconduct even if the tool runs locally.

---

## 3. Using Binary Lab on exam/proctoring software safely

If you have written authorization and a copy you may inspect:

1. Work on a copy. Record the SHA-256 in the report header.
2. Analyze statically only. Do not run the sample outside the authorized VM.
3. Use **Explain with AI** to interpret imports, resources and strings, remembering that findings
   are indicators, not proof.
4. Do not upload other people's data or live exam material.
5. Keep reports inside `data\reports\` on an encrypted drive, and delete them when the engagement
   ends.

The app cannot execute, unpack, emulate, or debug a binary. For behavior, use a dedicated sandbox
with authorization.

---

## 4. Reporting a security issue in this app

If you find a vulnerability in Portable AI, describe the impact and reproduction steps to the
person who provided the drive. Do not test it against systems you do not own. The intended threat
model is a single trusted user on a trusted PC; LAN mode is for a trusted local network only.
