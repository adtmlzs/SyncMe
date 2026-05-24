# SyncMe

Instagram DM bot that summarizes reels you share. When someone sends a reel link in a DM, SyncMe downloads the video, transcribes it with Groq Whisper, summarizes it with Llama, stores the result in Supabase, and replies with the summary.

## How it works

1. **Webhook** — Meta sends DM events to your FastAPI server.
2. **Download** — `yt-dlp` fetches the reel video locally.
3. **AI** — Groq Whisper transcribes audio; Llama produces a structured summary.
4. **Storage** — Summary is saved to Supabase (`reel_summaries` table).
5. **Reply** — Summary is sent back via the Instagram Graph API.

## Requirements

- Python 3.11+
- [Meta app](https://developers.facebook.com/) with Instagram messaging / webhooks
- [Groq API key](https://console.groq.com/)
- [Supabase](https://supabase.com/) project

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # then fill in your keys
```

Create the database table in Supabase (SQL editor):

```sql
CREATE TABLE IF NOT EXISTS reel_summaries (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    original_url TEXT        NOT NULL,
    ai_summary   JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reel_summaries_user ON reel_summaries (user_id);
```

## Run locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Expose port 8000 with [ngrok](https://ngrok.com/) (or similar) and set the webhook URL in the Meta developer dashboard to `https://<your-tunnel>/webhook`.

## Environment variables

| Variable | Description |
|----------|-------------|
| `META_VERIFY_TOKEN` | Custom string for webhook verification |
| `META_APP_SECRET` | App secret from Meta dashboard |
| `META_ACCESS_TOKEN` | Page / Instagram access token |
| `GROQ_API_KEY` | Groq API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service role key |

See `.env.example` for a template.

## Project layout

| File | Role |
|------|------|
| `main.py` | FastAPI app, webhook handlers, background pipeline |
| `scraper.py` | Reel download via yt-dlp |
| `ai_processor.py` | Whisper + Llama summarization |
| `database.py` | Supabase inserts with retry |
| `messenger.py` | Instagram DM replies |

## License

MIT (add a `LICENSE` file if you publish publicly).
