<div align="center">
  <br/>
  <img src="assets/logo.png" alt="NutriConnect" width="72"/>
  <h1>NutriConnect</h1>
  <p>An AI nutrition co-pilot that delivers personalized guidance<br/>at the moment of decision — not after the fact.</p>

  <p>
    <a href="https://nutriconnect-69ww.onrender.com"><img src="https://img.shields.io/badge/Live%20App-1D9E75?style=flat-square&logo=render&logoColor=white" alt="Live App"/></a>
    <a href="https://youtu.be/TX4KEq-VgJ0"><img src="https://img.shields.io/badge/Demo%20Video-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="Demo"/></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-6366F1?style=flat-square" alt="MIT"/></a>
    <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white"/>
    <img src="https://img.shields.io/badge/Flask-3.0-000000?style=flat-square&logo=flask&logoColor=white"/>
    <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white"/>
    <img src="https://img.shields.io/badge/Groq%20AI-F55036?style=flat-square&logoColor=white"/>
  </p>

  <p><sub>Official submission · <strong>AI Infinity BuildFest 2026</strong></sub></p>
  <br/>
</div>

---

## The Problem

Most people decide what to eat in under 10 seconds. Existing nutrition tools — calorie counters, food databases, dietitian apps — are built for reflection, not real-time decision support. Guidance arrives after the choice is already made, if at all.

People managing diabetes, food allergies, or cardiovascular disease navigate complex dietary decisions daily, with no personalized support at the point of choice. Recurring dietitian consultations are effective but expensive and inaccessible to most.

---

## Solution

NutriConnect puts an AI co-pilot at the moment of decision. It learns your health profile — conditions, restrictions, goals — and delivers instant, context-aware guidance the moment you need it. Not a passive log. Not generic meal plans. A system that knows *you*.

> **Live demo:** [nutriconnect-69ww.onrender.com](https://nutriconnect-69ww.onrender.com) &nbsp;·&nbsp; **Video:** [youtu.be/TX4KEq-VgJ0](https://youtu.be/TX4KEq-VgJ0)

---

## Features

| | Feature | Description |
|---|---|---|
| ✦ | **Personalized meal recommendations** | AI-curated suggestions based on health profile, restrictions, and caloric targets |
| ✦ | **Real-time nutrition analysis** | Full macro and micronutrient breakdown per meal, delivered instantly |
| ✦ | **"Can I eat this?" checker** | Instant AI verdict on any food item against your personal health context |
| ✦ | **Calorie & macro tracker** | Daily meal logging with live counters and balance indicators |
| ✦ | **Health-aware food discovery** | AI-ranked library filterable by dietary preference, cuisine, and health goal |
| ✦ | **Nutrition dashboard** | Visual trends, goal progress, macro distribution, and weekly insights |

---

## Screenshots

<table>
  <tr>
    <td align="center"><b>Home</b><br/><img src="assets/home.png" width="100%"/></td>
    <td align="center"><b>Dashboard</b><br/><img src="assets/dashboard.png" width="100%"/></td>
  </tr>
  <tr>
    <td align="center"><b>AI Nutrition Analysis</b><br/><img src="assets/analysis.png" width="100%"/></td>
    <td align="center"><b>Meal Planner</b><br/><img src="assets/meal-planner.png" width="100%"/></td>
  </tr>
</table>

---

## Architecture

```
Browser (HTML5 · Bootstrap 5.3 · Chart.js)
        │
        ▼
Flask 3.0  ──────────────────────► Groq AI API
        │                          (LLM inference)
        ▼
SQLAlchemy ORM ──► PostgreSQL 14
```

### Stack

| Layer | Technology |
|---|---|
| Frontend | HTML5, CSS3, JavaScript |
| UI framework | Bootstrap 5.3 |
| Backend | Python 3.11, Flask 3.0 |
| Database | PostgreSQL 14, SQLAlchemy ORM |
| AI engine | Groq AI API |
| Charts | Chart.js |
| Deployment | Render (Gunicorn + Procfile) |

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- A [Groq API key](https://console.groq.com)

### Setup

```bash
# 1. Clone
git clone https://github.com/tamannajarin04/nutriconnect.git
cd nutriconnect

# 2. Virtual environment
python3 -m venv venv && source venv/bin/activate

# 3. Dependencies
pip install -r requirements.txt

# 4. Environment variables
cp .env.example .env
```

Edit `.env`:

```env
FLASK_APP=run.py
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
DATABASE_URL=postgresql://username:password@localhost:5432/nutriconnect
GROQ_API_KEY=your-groq-api-key-here
```

```bash
# 5. Database
python create_db.py
flask db migrate -m "Initial migration"
flask db upgrade

# 6. Run
python run.py
# → http://localhost:5000
```

---

## Project Structure

```
nutriconnect/
├── app/
│   ├── static/
│   │   ├── css/            # Component stylesheets
│   │   ├── js/             # Frontend logic and API handlers
│   │   └── assets/         # Images and icons
│   ├── templates/          # Jinja2 HTML templates
│   ├── models/             # SQLAlchemy models and AI pipelines
│   ├── routes/             # Flask blueprints
│   └── __init__.py         # Application factory
├── migrations/             # Alembic migration files
├── config.py               # Environment configuration
├── run.py                  # Entry point
├── create_db.py            # Database initialization
├── Procfile                # Gunicorn deployment config
└── requirements.txt        # Python dependencies
```

---

## Deployment

Deployed on [Render](https://render.com) via Gunicorn. The `Procfile` configures this automatically.

```bash
gunicorn run:app
```

For self-hosted setups, place Gunicorn behind an Nginx reverse proxy. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full guide.

---

## Why NutriConnect

| The gap | Our approach |
|---|---|
| Guidance arrives too late | AI at the point of decision — before you choose |
| Generic plans ignore conditions | Profile-aware: diabetes, allergies, cardiovascular goals |
| Dietitians are inaccessible | Always-on nutrition intelligence, no appointment needed |
| Logging without insight | Active recommendations, not passive tracking |

---

## License

MIT License — see [`LICENSE`](LICENSE) for details.

---

<div align="center">
  <sub>
    NutriConnect &nbsp;·&nbsp; AI Infinity BuildFest 2026 &nbsp;·&nbsp;
    <a href="https://github.com/tamannajarin04/nutriconnect">GitHub</a> &nbsp;·&nbsp;
    <a href="https://nutriconnect-69ww.onrender.com">Live App</a> &nbsp;·&nbsp;
    <a href="https://youtu.be/TX4KEq-VgJ0">Demo</a>
  </sub>
</div>
