FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py email_sender.py testar_smtp.py modelo_certificado.pdf ./

ENV DATABASE_PATH=/data/certificados.db \
    OUTPUT_DIR=/data/certificados_gerados

RUN mkdir -p /data

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
