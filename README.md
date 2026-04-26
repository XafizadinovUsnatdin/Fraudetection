# SafeNet AI Anti-Fraud Demo
#first version https://safenetfraud.streamlit.app/
random forest model

second version deep learning https://fraudetectionsafenet.streamlit.app/
  
## Streamlit Fraud Detection App







<img width="1863" height="756" alt="image" src="https://github.com/user-attachments/assets/535fbbc0-d97b-4a3e-b441-4594d93404da" />

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
- Test ROC-AUC: taxminan 0.90,

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
`ml/fraud_model_v2.pkl` fayliga saqlaydi.
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
MOdelni sinab ko'rish qo'shimcha admin panel korinishida:   
<img width="1950" height="942" alt="image" src="https://github.com/user-attachments/assets/8e3f93d6-8b9e-4638-9808-3207c2e5aee6" />



## Final Demo Simulation


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


```bash
python scripts\simulate_transactions.py --api-url http://localhost:8001
```

team experience 
president tech award with Tahlilchi AI project tahlilchi-ai.uz
<img width="1280" height="778" alt="image" src="https://github.com/user-attachments/assets/1ccb5a9e-94d3-4586-b9cb-144e58056b5b" />

AIFU hackaton Tahlilchi AI — "Eng innovatsion AI yechim" nominatsiyasi g‘olibi

<img width="1280" height="720" alt="image" src="https://github.com/user-attachments/assets/ba01c902-0c4e-4532-9dad-c79e234e1239" />

Kelakak muhandislar 2025 hackaton g'olibi
<img width="640" height="640" alt="image" src="https://github.com/user-attachments/assets/29d5dab8-e9de-4b39-8f53-bda4128cad11" />


