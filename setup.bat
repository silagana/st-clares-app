@echo off
REM Setup para Windows
python -m venv .venv
call .venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Setup completo.
echo Para correr la app:
echo   .venv\Scripts\activate
echo   streamlit run app.py
