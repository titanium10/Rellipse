# Rellipse

**AI-powered reply generator for customer messages across all platforms**

![Status](https://img.shields.io/badge/status-live-brightgreen)
![Python](https://img.shields.io/badge/python-3.9+-blue)

[Live Demo](https://replyze.onrender.com/) • [GitHub](https://github.com/titanium10/replyze)

## Features

- **Paste a review/message** → get an AI reply in seconds
- **Works on** Google Maps, WhatsApp, Instagram, Facebook, Twitter, Email
- **Tone control** (formal to casual slider)
- **Language support** (11 languages)
- **Reply history** with scroll-reveal animations
- **Editable replies** before copying
- **PWA** (installable on phone)
- **Zero data stored** — your messages are never logged

## Tech Stack

- **Backend:** Flask + Python
- **AI:** Anthropic Claude API (claude-haiku-4-5)
- **Database:** SQLite
- **Frontend:** Vanilla HTML/CSS/JS with liquid glass UI
- **Auth:** Google OAuth + email/password
- **Deployment:** Render
- **Pricing:** PPP (Pay-Per-Person) — ₹299 India, $19 USA, etc.

## Getting Started

```bash
git clone https://github.com/titanium10/replyze
cd replyze
pip install -r requirements.txt
export SECRET_KEY=your-secret
export GOOGLE_CLIENT_ID=your-client-id
export GOOGLE_CLIENT_SECRET=your-secret
export ANTHROPIC_API_KEY=your-api-key
python app.py
```

Visit `http://localhost:5001`

## Live Demo

[https://replyze.onrender.com](https://replyze.onrender.com/)

## Built by

14-year-old developer [@samratbuilds](https://x.com/samratbuilds)

---

Stop drowning in customer messages. Reply in seconds, not hours.
