# SafeNet AI Anti-Fraud Demo

## Streamlit Fraud Detection App

Asosiy demo Streamlit orqali ishlaydi:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Brauzerda oching:

```text
http://localhost:8501
```

Joriy ML sozlamalari:

- Dataset: `data/synthetic_transactions.csv`
- Hajm: 50,000 qator, 23 ustun
- Fraud ulushi: 5%
- Model: Random Forest + 5-fold stratifikatsiyali cross-validation
- Test ROC-AUC: taxminan 0.90, ya'ni 100% mukammal emas va real hayotga yaqinroq

Datasetni qayta yaratish:

```bash
python data\generate_dataset.py
```

Modelni Streamlit ichidagi **O'rgatish** tabidan qayta o'rgatish mumkin.

## Streamlit Cloud Deploy

Streamlit Cloud sozlamalari:

- Repository: `XafizadinovUsnatdin/Fraudetection`
- Branch: `main`
- Main file path: `streamlit_app.py`
- Python: `runtime.txt` orqali `python-3.11`

Model `.pkl` fayllari repoga qo'shilmaydi. Deploy paytida app birinchi ishga tushganda
`data/synthetic_transactions.csv`dan yengil start modelni avtomatik o'rgatadi va vaqtincha
`ml/fraud_model_v2.pkl` fayliga saqlaydi. To'liq 5-fold modelni keyin Streamlit ichidagi
**O'rgatish** tabidan qayta o'rgatish mumkin. Bu Streamlit Cloud cold-start vaqtini qisqartiradi
va Python/sklearn/numpy versiya mos kelmasligi sababli pickle yuklanmay qolish muammosini oldini oladi.

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
