"""
DROP - 한정판 상품 선착순 판매 API

저장 방식
- 요청은 메모리(state)에서 처리하고, 데이터가 바뀔 때만 JSON 파일(data.json)에 저장한다.
- 재고 확인 → 차감 → 주문 저장 → 파일 저장을 하나의 Lock 안에서 처리해
  동시 요청이 몰려도 재고보다 많이 팔리지 않게 한다.
- 파일은 임시 파일에 먼저 쓴 뒤 os.replace로 교체해, 저장 중 장애가 나도 파일이 깨지지 않게 한다.
- 이 방식은 프로세스 1개에서만 올바르게 동작한다(gunicorn -w 1).
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DATA_FILE = os.path.join(DATA_DIR, "data.json")
SEED_FILE = os.path.join(BASE_DIR, "seed.json")

KST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.json.ensure_ascii = False

lock = threading.Lock()
state = {}


# ---------------------------------------------------------------------------
# 시간·입력 처리
# ---------------------------------------------------------------------------
def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(KST).isoformat(timespec="seconds")


def parse_time(text):
    """ISO 문자열을 시간으로 변환한다. 시간대가 없으면 한국 시간으로 본다."""
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def normalize_contact(text):
    """연락처의 공백·하이픈을 없애고 소문자로 맞춘다. (010-1234-5678 = 01012345678)"""
    return re.sub(r"[\s-]", "", text).lower()


def error(status, code, message):
    return jsonify({"error": code, "message": message}), status


# ---------------------------------------------------------------------------
# JSON 파일 저장·불러오기
# ---------------------------------------------------------------------------
def save_state():
    """반드시 lock을 잡은 상태에서 호출한다."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DATA_FILE)


def load_state():
    global state
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            state = json.load(f)
        app.logger.info("data.json 불러옴: %s", DATA_FILE)
    else:
        with open(SEED_FILE, encoding="utf-8") as f:
            seed = json.load(f)
        state = {"next_order_id": 1, "products": seed["products"], "orders": {}}
        save_state()
        app.logger.info("seed.json으로 초기 데이터 생성: %s", DATA_FILE)


# ---------------------------------------------------------------------------
# 응답 형태
# ---------------------------------------------------------------------------
def product_public(pid, p, current):
    if current < parse_time(p["sale_start"]):
        status = "UPCOMING"
    elif p["stock"] <= 0:
        status = "SOLD_OUT"
    else:
        status = "ON_SALE"
    return {
        "id": pid,
        "name": p["name"],
        "description": p.get("description", ""),
        "price": p["price"],
        "stock": p["stock"],
        "total_stock": p.get("total_stock", p["stock"]),
        "sale_start": p["sale_start"],
        "image_url": p.get("image_url", ""),
        "status": status,
    }


def order_public(oid, o):
    return {
        "order_id": oid,
        "product_id": o["product_id"],
        "product_name": o["product_name"],
        "nickname": o["nickname"],
        "status": o["status"],
        "created_at": o["created_at"],
        "canceled_at": o.get("canceled_at"),
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "server_time": iso(now())})


@app.get("/api/products")
def list_products():
    current = now()
    with lock:
        items = [
            product_public(pid, p, current)
            for pid, p in sorted(state["products"].items(), key=lambda kv: int(kv[0]))
        ]
    return jsonify({"server_time": iso(current), "products": items})


@app.get("/api/products/<pid>")
def get_product(pid):
    current = now()
    with lock:
        p = state["products"].get(pid)
        if p is None:
            return error(404, "NOT_FOUND", "존재하지 않는 상품입니다.")
        item = product_public(pid, p, current)
    return jsonify({"server_time": iso(current), "product": item})


@app.post("/api/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    pid = str(data.get("product_id", "")).strip()
    nickname = str(data.get("nickname", "")).strip()
    contact = normalize_contact(str(data.get("contact", "")))

    if not pid:
        return error(400, "INVALID_INPUT", "상품을 선택해 주세요.")
    if not 1 <= len(nickname) <= 20:
        return error(400, "INVALID_INPUT", "닉네임은 1~20자로 입력해 주세요.")
    if not 4 <= len(contact) <= 50:
        return error(400, "INVALID_INPUT", "연락처는 4~50자로 입력해 주세요.")

    with lock:
        p = state["products"].get(pid)
        if p is None:
            return error(404, "NOT_FOUND", "존재하지 않는 상품입니다.")

        current = now()
        start = parse_time(p["sale_start"])
        if current < start:
            return error(400, "NOT_STARTED",
                         f"아직 판매 전입니다. {iso(start)}에 판매를 시작합니다.")

        already = any(
            o["product_id"] == pid and o["user_key"] == contact and o["status"] == "ORDERED"
            for o in state["orders"].values()
        )
        if already:
            return error(409, "ALREADY_ORDERED", "이 상품은 1인 1개까지만 구매할 수 있습니다.")

        if p["stock"] <= 0:
            return error(409, "SOLD_OUT", "품절되었습니다.")

        oid = str(state["next_order_id"])
        order = {
            "product_id": pid,
            "product_name": p["name"],
            "nickname": nickname,
            "user_key": contact,
            "status": "ORDERED",
            "created_at": iso(current),
        }
        p["stock"] -= 1
        state["orders"][oid] = order
        state["next_order_id"] += 1

        try:
            save_state()
        except OSError:
            # 파일 저장에 실패하면 메모리 변경도 되돌린다.
            p["stock"] += 1
            del state["orders"][oid]
            state["next_order_id"] -= 1
            app.logger.exception("주문 저장 실패")
            return error(500, "SAVE_FAILED", "주문을 저장하지 못했습니다. 다시 시도해 주세요.")

        result = order_public(oid, order)
    return jsonify({"order": result}), 201


@app.get("/api/orders")
def list_orders():
    contact = normalize_contact(request.args.get("contact", ""))
    if not 4 <= len(contact) <= 50:
        return error(400, "INVALID_INPUT", "주문할 때 입력한 연락처를 입력해 주세요.")
    with lock:
        items = [
            order_public(oid, o)
            for oid, o in sorted(state["orders"].items(), key=lambda kv: -int(kv[0]))
            if o["user_key"] == contact
        ]
    return jsonify({"orders": items})


@app.post("/api/orders/<oid>/cancel")
def cancel_order(oid):
    data = request.get_json(silent=True) or {}
    contact = normalize_contact(str(data.get("contact", "")))

    with lock:
        o = state["orders"].get(oid)
        if o is None:
            return error(404, "NOT_FOUND", "존재하지 않는 주문입니다.")
        if o["user_key"] != contact:
            return error(403, "FORBIDDEN", "주문할 때 입력한 연락처와 다릅니다.")
        if o["status"] == "CANCELED":
            return error(409, "ALREADY_CANCELED", "이미 취소된 주문입니다.")

        p = state["products"].get(o["product_id"])
        o["status"] = "CANCELED"
        o["canceled_at"] = iso(now())
        if p is not None:
            p["stock"] += 1

        try:
            save_state()
        except OSError:
            o["status"] = "ORDERED"
            o.pop("canceled_at", None)
            if p is not None:
                p["stock"] -= 1
            app.logger.exception("취소 저장 실패")
            return error(500, "SAVE_FAILED", "취소를 저장하지 못했습니다. 다시 시도해 주세요.")

        result = order_public(oid, o)
    return jsonify({"order": result})


@app.errorhandler(404)
def not_found(_e):
    return error(404, "NOT_FOUND", "요청한 주소를 찾을 수 없습니다.")


@app.errorhandler(405)
def method_not_allowed(_e):
    return error(405, "METHOD_NOT_ALLOWED", "허용되지 않은 요청 방식입니다.")


load_state()

if __name__ == "__main__":
    # Docker 없이 로컬에서 직접 실행할 때: python app.py
    app.run(host="0.0.0.0", port=5000, threaded=True)
