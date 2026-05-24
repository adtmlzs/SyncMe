# 🔄 SyncMe — AI-Powered Instagram Reel Summarizer

> **Forward an Instagram Reel → Get an instant AI summary in your DMs.**

SyncMe is an asynchronous, production-grade Instagram DM bot built with FastAPI. When a user sends a Reel link, SyncMe downloads the video, transcribes the audio with Groq Whisper, generates a structured summary with Llama 3.3, stores the result in Supabase, and replies — all within seconds, without ever blocking the server.

```
User sends Reel ──▶ Webhook ──▶ yt-dlp Download ──▶ Whisper Transcription
                                                           │
                   DM Reply  ◀── Supabase Store  ◀── Llama Summary
```

**Example reply:**
```
🎯 The reel explains three morning habits that boost productivity by 40%.

📌 Key Points:
  • Wake up 30 minutes earlier and avoid screens for the first hour.
  • Use a 2-minute journal to set daily priorities.
  • Cold exposure (shower or face splash) activates the sympathetic nervous system.

🏷️ #productivity #morningroutine #habits #selfimprovement
```

---

## 🏗️ Architecture Highlights

| Concept | Implementation |
|---|---|
| **Non-blocking webhooks** | `POST /webhook` returns `200 OK` instantly. The download → transcribe → summarise → reply pipeline runs as a `BackgroundTask`, never blocking the event loop. |
| **Thread-safe scraping** | `yt-dlp` is synchronous — SyncMe wraps it in `asyncio.to_thread()` so the FastAPI event loop stays free for concurrent requests. |
| **Auto-cleanup of `.mp4` files** | Every download writes to a `tempfile.TemporaryDirectory`. The context manager guarantees all files are purged from disk even if the pipeline crashes. Zero orphaned media. |
| **Stateless cookie auth** | Instagram cookies are stored as a Base64-encoded env var (`IG_COOKIE_BASE64`). At server startup, they're decoded to a temp file — no raw secrets on disk, no persistent filesystem needed. |
| **Exponential-backoff retry** | Supabase inserts retry up to 3× with exponential backoff (1s → 2s → 4s). Client errors (4xx) fail fast; only transient 5xx/network errors are retried. |
| **In-memory rate limiting** | Sliding-window limiter (2 reels / 60s per user) prevents abuse without external dependencies. |
| **Webhook signature verification** | Validates `X-Hub-Signature-256` using HMAC-SHA256 against `META_APP_SECRET` to reject forged payloads. |

---

## 📁 Project Layout

```
SyncMe/
├── main.py            # FastAPI app, webhook handlers, background pipeline, legal pages
├── scraper.py         # Reel download via yt-dlp (async wrapper)
├── ai_processor.py    # Whisper transcription + Llama summarisation (Groq API)
├── database.py        # Supabase PostgREST inserts with retry logic
├── messenger.py       # Instagram DM replies via Graph API
├── requirements.txt   # Python dependencies
├── .env.example       # Environment variable template
└── .gitignore         # Keeps secrets and media out of version control
```

---

## ✅ Prerequisites

Before you begin, make sure you have accounts and credentials for the following services:

| Service | What You Need | Link |
|---|---|---|
| **Meta for Developers** | A Business App with Instagram Messaging permissions | [developers.facebook.com](https://developers.facebook.com/) |
| **Groq** | API key (free tier available) | [console.groq.com](https://console.groq.com/) |
| **Supabase** | Project URL + Service Role Key | [supabase.com](https://supabase.com/) |
| **Railway** | Account for deployment (or any container host) | [railway.app](https://railway.app/) |
| **Instagram** | A dedicated account for the bot to operate from | [instagram.com](https://www.instagram.com/) |
| **Python** | Version 3.11 or higher | [python.org](https://www.python.org/) |

---

## 🗄️ Phase 1 — Database Setup

Open the **SQL Editor** in your Supabase dashboard and run the following script to create the `reel_summaries` table:

```sql
-- ── SyncMe: reel_summaries table ──────────────────────────────────
CREATE TABLE IF NOT EXISTS reel_summaries (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    original_url TEXT        NOT NULL,
    ai_summary   JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast lookups by user
CREATE INDEX idx_reel_summaries_user ON reel_summaries (user_id);
```

> [!TIP]
> The `ai_summary` column uses `JSONB` so you can query individual fields (thesis, tags, etc.) directly with Postgres JSON operators later.

---

## 🔐 Phase 2 — Instagram Cookie Authentication (Crucial)

Instagram requires authentication for yt-dlp to download Reels. SyncMe uses a **stateless cookie injection** approach: you export cookies from your browser, Base64-encode them, and store the encoded string as an environment variable. At runtime, the server decodes them to a temporary file — no raw cookie files ever touch your repo.

### Step 1: Export Cookies

1. Log in to [instagram.com](https://www.instagram.com/) with your bot's Instagram account in **Chrome** or **Firefox**.
2. Install a cookie export extension:
   - Chrome: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - Firefox: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
3. Navigate to `instagram.com`, click the extension icon, and export cookies in **Netscape format**.
4. Save the file as `cookies.txt` in a secure local directory.

### Step 2: Base64 Encode

Encode the cookie file into a single Base64 string:

**macOS / Linux:**
```bash
base64 -w 0 cookies.txt
```

**Windows (PowerShell):**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))
```

Copy the entire output string — this is your `IG_COOKIE_BASE64` value.

### Step 3: Set the Environment Variable

Add the encoded string to your `.env` file or deployment environment:

```env
IG_COOKIE_BASE64=<your_base64_encoded_string_here>
IG_COOKIES_PATH=/tmp/ig_cookies.txt
```

> [!CAUTION]
> **NEVER commit `cookies.txt` or your `.env` file to GitHub.** Your Instagram session cookies grant full account access. The `.gitignore` already excludes both files — do not override this. If cookies are leaked, immediately log out of all sessions in the Instagram app.

> [!NOTE]
> Instagram cookies expire periodically. If the bot starts failing with "login required" errors, re-export fresh cookies and update `IG_COOKIE_BASE64`.

---

## 💻 Phase 3 — Local Setup

### Clone & Install

```bash
git clone https://github.com/adtmlzs/SyncMe.git
cd SyncMe

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```env
# ── Meta / Instagram Webhook ───────────────────────────────────────
META_VERIFY_TOKEN=your_custom_verification_string
META_APP_SECRET=your_meta_app_secret
META_ACCESS_TOKEN=your_page_access_token

# ── Groq (Whisper transcription + Llama summarisation) ─────────────
GROQ_API_KEY=gsk_your_groq_api_key

# ── Supabase ───────────────────────────────────────────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_role_key

# ── Instagram Cookies (see Phase 2) ───────────────────────────────
IG_COOKIE_BASE64=your_base64_encoded_cookies
IG_COOKIES_PATH=/tmp/ig_cookies.txt

# ── Sentry (optional — error tracking) ────────────────────────────
SENTRY_DSN=https://your-sentry-dsn
```

### Run the Server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Expose Locally with ngrok

For Meta to reach your local server during development, expose port 8000 with [ngrok](https://ngrok.com/):

```bash
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL — you'll need it for the Meta webhook setup in Phase 5.

---

## 🚀 Phase 4 — Railway Deployment

1. **Connect GitHub** — Link your Railway account to the GitHub repo at [railway.app](https://railway.app/).
2. **Create a new project** — Select "Deploy from GitHub repo" and choose `SyncMe`.
3. **Inject environment variables** — In the Railway service settings, add every variable from your `.env` file:

   | Variable | Value |
   |---|---|
   | `META_VERIFY_TOKEN` | Your custom string |
   | `META_APP_SECRET` | From Meta dashboard |
   | `META_ACCESS_TOKEN` | Page access token |
   | `GROQ_API_KEY` | From Groq console |
   | `SUPABASE_URL` | Your Supabase project URL |
   | `SUPABASE_KEY` | Supabase service role key |
   | `IG_COOKIE_BASE64` | Base64-encoded cookies |
   | `IG_COOKIES_PATH` | `/tmp/ig_cookies.txt` |
   | `SENTRY_DSN` | *(optional)* Sentry DSN |

4. **Set the start command** — Railway auto-detects Python, but ensure the start command is:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
5. **Generate a public domain** — In the service's **Settings → Networking**, generate a Railway domain (e.g., `syncme-production.up.railway.app`).
6. **Deploy** — Push to `main` and Railway will auto-deploy. Check the logs for `SyncMe is starting up 🚀`.

---

## 📱 Phase 5 — Meta Developer Dashboard Setup

This is the most involved step, but every action is spelled out below. If this is your first time working with the Meta developer platform, read each step fully before clicking anything.

> [!IMPORTANT]
> **Before you start:** Your bot's Instagram account **must** be a Professional Account (Creator or Business). If it's still a personal account, switch it first:
> Open the Instagram app → Go to your bot's profile → **Settings → Account type and tools → Switch to Professional Account** → Choose **Creator** or **Business** → Complete the prompts.

---

### Step 1: Create a Meta Business App

1. Open [developers.facebook.com](https://developers.facebook.com/) in your browser and log in with the **Facebook account** that owns (or is linked to) your bot's Instagram Professional Account.
2. Click **My Apps** in the top-right corner.
3. Click the green **Create App** button.
4. Under "What do you want your app to do?", select **"Other"** → click **Next**.
5. Select **"Business"** as the app type → click **Next**.
6. Fill in the details:
   - **App name**: e.g., `SyncMe Bot`
   - **App contact email**: your email address
   - **Business Account**: select your business account, or skip if you don't have one
7. Click **Create App**. You may be asked to re-enter your Facebook password.
8. You should now be on your **App Dashboard**. Note the **App ID** shown at the top — you'll need it later.

---

### Step 2: Add the Instagram Messaging Product

1. On the App Dashboard, look at the left sidebar. Click **"Add Products"** (or scroll down to the product list on the main page).
2. Find **"Instagram"** in the product list and click **"Set Up"**.
3. You'll see multiple options — select **"Instagram Messaging"** (sometimes labelled "Messenger Platform for Instagram").

> [!WARNING]
> Do **not** select "Instagram Basic Display" or "Instagram Login" — those are different products and will not give you DM webhook access.

4. After adding it, you should see **"Instagram"** appear in the left sidebar with sub-items like **API Setup**, **Webhooks**, etc.

---

### Step 3: Generate the Access Token

This token allows your bot to send DM replies through the Instagram Graph API.

1. In the left sidebar, click **Instagram → API Setup**.
2. You'll see a section called **"Generate Access Tokens"** (or "Token Generation").
3. Click the **"Add Account"** or **"Connect"** button next to your bot's Instagram Professional Account.
   - If you don't see your Instagram account listed, make sure:
     - The Instagram account is a **Professional Account** (Creator or Business).
     - The Instagram account is **linked to a Facebook Page** (Instagram Settings → Linked Accounts → Facebook).
4. A popup will appear asking you to authorise permissions. **Check all the boxes** (messages, manage messages, etc.) and click **"Generate Token"**.
5. A long token string will appear — **this is your `META_ACCESS_TOKEN`**.

> [!CAUTION]
> **Copy this token RIGHT NOW and paste it into your `.env` file or Railway variables.** The token is only displayed once. If you close the popup without copying, you'll need to regenerate it.

---

### Step 4: Get the App Secret

The App Secret is used to verify that incoming webhook payloads actually come from Meta (not a forged request).

1. In the left sidebar, click **App Settings → Basic**.
2. You'll see a field called **"App Secret"** — it's hidden by default.
3. Click the **"Show"** button next to it. You may be asked to re-enter your Facebook password.
4. Copy the revealed string — **this is your `META_APP_SECRET`**.
5. Paste it into your `.env` file or Railway variables.

---

### Step 5: Configure the Webhook

The webhook is how Meta tells your server "hey, someone just sent a DM to your bot." You need to give Meta a public URL where it can send these notifications.

> [!IMPORTANT]
> **Your server must be running and publicly accessible BEFORE you do this step.** Meta will immediately send a verification request to your URL. If your server isn't running, verification will fail.
>
> - **If deploying on Railway:** Make sure your app is deployed and you have a public Railway domain (e.g., `syncme-production.up.railway.app`).
> - **If developing locally:** Make sure `uvicorn` is running and ngrok is exposing port 8000 (see Phase 3).

1. In the left sidebar, click **Instagram → Webhooks**.
2. Click the **"Configure"** button (or **"Edit Subscription"** if you've set one up before).
3. A popup will appear with two fields:
   - **Callback URL**: Enter your full webhook URL. Examples:
     - Railway: `https://syncme-production.up.railway.app/webhook`
     - ngrok: `https://a1b2c3d4.ngrok-free.app/webhook`
   - **Verify Token**: Enter the **exact same string** you set as `META_VERIFY_TOKEN` in your `.env` / Railway variables. This can be any string you choose (e.g., `my_super_secret_token_123`), but it **must match exactly** between your server and this field.
4. Click **"Verify and Save"**.
5. **What happens behind the scenes:** Meta sends a `GET` request to your Callback URL with a challenge parameter. Your FastAPI server receives it, checks that the verify token matches, and echoes the challenge back. If everything matches, Meta shows a green success message.

**If verification fails**, check:
- Is your server actually running? Check the logs.
- Is the URL correct? Try opening `https://your-url/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123` in your browser — you should see `test123` as the response.
- Does `META_VERIFY_TOKEN` in your server's environment **exactly** match what you typed in the Meta dashboard? (Watch for trailing spaces.)

---

### Step 6: Subscribe to Messaging Events

After the webhook is verified, you need to tell Meta which specific events to forward to your server.

1. You should still be on the **Instagram → Webhooks** page.
2. You'll see a table listing webhook fields (events) like `messages`, `messaging_postbacks`, `messaging_optins`, etc.
3. Find the row for **`messages`** and click the **"Subscribe"** toggle/button to turn it **ON**.
4. You do **not** need to subscribe to any other field — `messages` is the only event SyncMe uses.

---

### Step 7: Add Instagram Testers (Required for Testing)

> [!IMPORTANT]
> While your Meta App is in **Development Mode** (which it is by default), only approved testers can interact with your bot. If you skip this step, DMs sent to your bot will **not** trigger any webhook events — your server will receive nothing and it will seem like the bot is broken.

This step adds Instagram accounts that are allowed to DM the bot during development.

#### 7a: Add a Tester in the Meta Dashboard

1. In the left sidebar, click **App Roles → Roles** (or **App Settings → Advanced → Roles**, depending on your dashboard version).
2. You should see a section called **"People"** or **"Instagram Testers"**.
3. Click **"Add Instagram Testers"**.
4. In the popup, type the **Instagram username** of the account you want to test with (this is the personal account that will DM the bot — not the bot's own account).
5. Click **"Submit"** to send the tester invitation.
6. You can add multiple testers by repeating this process.

#### 7b: Accept the Tester Invitation on Instagram

The person you just added must now **accept** the invitation from the Instagram app. This is done on the tester's phone/browser, NOT in the Meta developer dashboard.

1. Open the **Instagram app** (or website) and log in with the **tester's account** (the one you just invited).
2. Go to **Settings**:
   - On mobile: Tap your profile picture → tap the **☰ hamburger menu** (top-right) → tap **Settings and privacy**.
   - On web: Click your profile picture → **Settings**.
3. Scroll down and tap **Website Permissions** (on some versions it may be under **Security → Apps and Websites**).
4. Tap **"Tester Invites"** (or **"App Invitations"**).
5. You should see an invitation from your app (e.g., `SyncMe Bot`). Tap **"Accept"**.

> [!NOTE]
> If you don't see "Tester Invites" in your settings:
> - Make sure you're logged into the **correct Instagram account** (the one that was invited, not the bot account).
> - Try navigating directly to: **Settings → Security → Apps and Websites → Tester Invites**.
> - The invitation can take a few minutes to appear. Wait 2–5 minutes and refresh.
> - On newer Instagram versions, the path may be: **Settings → Website Permissions → Tester Invites**.

#### 7c: Verify the Tester Was Added

1. Go back to the Meta Developer Dashboard.
2. Under **App Roles → Roles**, the tester should now show as **"Accepted"** (with a green status).
3. This account can now DM the bot and trigger webhook events.

---

### Step 8: Test It End-to-End

Now everything is wired up. Let's test the full pipeline.

1. Open **Instagram** on the **tester's account** (the one you added and accepted in Step 7).
2. Find your **bot's Instagram account** and open a DM conversation with it.
3. Send a reel from your feed or a message containing an Instagram Reel link, for example:
   ```
   https://www.instagram.com/reel/ABC123/
   ```
4. Watch your server logs (Railway dashboard or your terminal). You should see the 4-stage pipeline execute:
   ```
   Stage 1/4 — Downloading reel...
   Stage 2/4 — Summarising with Groq...
   Stage 3/4 — Saving to Supabase...
   Stage 4/4 — Sending DM reply...
   ✅ Pipeline complete.
   ```
5. Go back to the Instagram DM — the bot should have replied with a formatted summary within 10–30 seconds.

**If the bot doesn't respond**, check this troubleshooting list:

| Symptom | Likely Cause | Fix |
|---|---|---|
| Server logs show **no incoming request** | Tester not added/accepted, or webhook not subscribed to `messages` | Complete Step 6 and Step 7 fully |
| Server logs show `Webhook verified successfully ✅` but no DM events | You tested the webhook verification (GET), but no DM was actually sent | Actually send a DM from the tester account — don't just open the webhook URL in a browser |
| `Webhook verification failed` in logs | `META_VERIFY_TOKEN` mismatch between server and Meta dashboard | Make sure the token string is **identical** in both places |
| `yt-dlp failed` or `login required` | Cookies expired or `IG_COOKIE_BASE64` not set | Re-export cookies (see Phase 2) and update the env var |
| `Supabase insert failed (HTTP 401)` | Wrong `SUPABASE_KEY` or table doesn't exist | Double-check your Supabase key and run the SQL from Phase 1 |
| `DM to user returned HTTP 400` | `META_ACCESS_TOKEN` expired or missing permissions | Regenerate the token in Step 3 with all permissions checked |
| Bot replies to itself in a loop | Bot is processing its own outgoing messages | This shouldn't happen — SyncMe only processes messages containing Reel URLs. If it does, check the sender ID filtering logic |

---

## 🔧 Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `META_VERIFY_TOKEN` | ✅ | Custom string for Meta webhook verification handshake |
| `META_APP_SECRET` | ✅ | App secret from Meta dashboard — used for `X-Hub-Signature-256` validation |
| `META_ACCESS_TOKEN` | ✅ | Page/Instagram access token for sending DM replies |
| `GROQ_API_KEY` | ✅ | API key from Groq console (powers Whisper + Llama) |
| `SUPABASE_URL` | ✅ | Your Supabase project URL (e.g., `https://abc.supabase.co`) |
| `SUPABASE_KEY` | ✅ | Supabase service role key (bypasses RLS) |
| `IG_COOKIE_BASE64` | ✅ | Base64-encoded Instagram cookies for yt-dlp authentication |
| `IG_COOKIES_PATH` | ⚙️ | Path to write decoded cookies (default: `/tmp/ig_cookies.txt`) |
| `SENTRY_DSN` | ❌ | Optional Sentry DSN for error tracking and alerting |

---

## ⚠️ Disclaimer

> [!WARNING]
> **This project is provided strictly for educational and hobby purposes.**
>
> Using `yt-dlp` to programmatically download Instagram content — especially with injected session cookies — **violates Meta's Terms of Service** regarding automated data collection. This approach may result in account suspension, rate limiting, or legal action from Meta.
>
> The author does **not** endorse using this code for commercial purposes, large-scale scraping, or any activity that infringes on Meta's platform policies or the intellectual property of content creators.
>
> **Use at your own risk.** This is a student passion project built to explore async Python, webhook architecture, and AI-powered content processing.

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).
