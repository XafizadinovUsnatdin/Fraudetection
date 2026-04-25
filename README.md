# SafeNet AI Anti-Fraud Demo

SafeNet is a hackathon-ready fintech anti-fraud demo with:

- ML fraud probability model
- FastAPI backend with SQLite
- React/Vite admin dashboard
- Final scripted simulation for presentation

## Quick Start

Create and activate the Python virtual environment:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Train or refresh the ML artifact if needed:

```bash
python ml\train_model.py
```

Run the backend:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Final Demo Simulation

The final demo script creates three transactions through the backend API:

1. Normal transaction: LOW risk, ALLOW
2. Suspicious transaction: MEDIUM risk, REVIEW
3. Fraud transaction: HIGH risk, BLOCK

Run from the repo root:

```bash
.\.venv\Scripts\activate
python scripts\simulate_transactions.py
```

The script defaults to:

```text
http://localhost:8000/transactions
```

If port `8000` is busy and the backend is running on `8001`:

```bash
python scripts\simulate_transactions.py --api-url http://localhost:8001
```

For full presentation notes, see:

```text
docs/demo_simulation.md
```

## Demo Flow

1. Start backend and frontend.
2. Open the dashboard at `http://localhost:5173`.
3. Run the simulation script.
4. Refresh the dashboard.
5. Show KPI cards, transaction history, fraud reasons, and decision chart.

## Useful Commands

Backend docs:

```text
http://localhost:8000/docs
```

Reset demo data:

```bash
del backend\fraud_demo.db
```

The backend will recreate the SQLite database and seed demo users on the next startup.
