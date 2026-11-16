FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py seed.json ./

ENV DATA_DIR=/data
EXPOSE 5000

# 워커(-w)는 반드시 1개: 재고·주문이 프로세스 메모리에 있으므로
# 워커가 여러 개면 워커마다 재고가 따로 존재해 초과 판매가 발생한다.
# 동시 요청은 스레드(--threads)로 처리하고, 재고 변경은 Lock으로 보호한다.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5000", "app:app"]
