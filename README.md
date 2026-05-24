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

This is the most involved step. Follow carefully.

### Step 1: Create a Meta Business App

1. Go to [developers.facebook.com](https://developers.facebook.com/) → **My Apps** → **Create App**.
2. Select **"Other"** as the use case, then **"Business"** as the app type.
3. Name it (e.g., `SyncMe Bot`) and create it.

### Step 2: Add the Instagram Messaging Product

1. In the App Dashboard, find **"Add Products"** in the left sidebar.
2. Locate **"Instagram"** and click **Set Up**.
3. Select **"Instagram Messaging"** (not Basic Display or Login).

### Step 3: Generate the Access Token

1. Navigate to **Instagram → API Setup** in the left sidebar.
2. Under **"Generate Token"**, connect your bot's Instagram Professional Account.
3. Click **Generate Token** — this is your `META_ACCESS_TOKEN`.

> [!IMPORTANT]
> Copy this token immediately — it is only shown once. Store it securely in your `.env` or Railway variables.

### Step 4: Get the App Secret

1. Go to **App Settings → Basic** in the left sidebar.
2. Under **"App Secret"**, click **Show** and copy the value.
3. This is your `META_APP_SECRET`.

### Step 5: Configure the Webhook

1. Navigate to **Instagram → Webhooks** in the left sidebar.
2. Click **"Configure"** (or "Edit Subscription").
3. Fill in:
   - **Callback URL**: `https://your-domain.up.railway.app/webhook` (your public server URL)
   - **Verify Token**: The exact value you set as `META_VERIFY_TOKEN` in your environment.
4. Click **"Verify and Save"**.

> [!NOTE]
> Your server must be running and publicly accessible when you click "Verify and Save". Meta sends a `GET /webhook` request with a challenge — your server echoes it back to prove ownership.

### Step 6: Subscribe to Messaging Events

1. After verification, you'll see a list of webhook fields.
2. **Subscribe** to the `messages` field — this is the only event SyncMe needs.

### Step 7: Test It

1. Open Instagram and DM your bot account with an Instagram Reel link (e.g., `https://www.instagram.com/reel/ABC123/`).
2. Watch your server logs — you should see the 4-stage pipeline execute:
   ```
   Stage 1/4 — Downloading reel...
   Stage 2/4 — Summarising with Groq...
   Stage 3/4 — Saving to Supabase...
   Stage 4/4 — Sending DM reply...
   ✅ Pipeline complete.
   ```
3. The bot should reply with a formatted summary within 10-30 seconds.

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
