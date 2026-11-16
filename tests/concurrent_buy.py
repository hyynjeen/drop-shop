"""
동시 구매 시험: 여러 사용자가 같은 순간에 같은 상품을 구매할 때 재고보다 많이 팔리지 않는지 확인한다.

사용법 (Python 3.8 이상, 추가 설치 없음)
    python tests/concurrent_buy.py --base http://localhost:8080 --product 1 --users 100
    python tests/concurrent_buy.py --base http://<EC2 퍼블릭 IP>:8080 --product 1 --users 100

통과 조건
    성공한 구매 수 == min(사용자 수, 시험 전 재고)  이고
    시험 후 재고 == 시험 전 재고 - 성공한 구매 수

같은 상품으로 다시 시험하려면 데이터를 초기화한다:
    docker compose down -v && docker compose up -d --build
"""
import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter


def call(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception as e:  # 연결 실패·시간 초과
        return 0, {"error": type(e).__name__}


def get_product(base, pid):
    status, body = call("GET", f"{base}/api/products/{pid}")
    if status != 200:
        sys.exit(f"상품 {pid} 조회 실패 (HTTP {status}): {body}")
    return body["product"]


def main():
    parser = argparse.ArgumentParser(description="DROP 동시 구매 시험")
    parser.add_argument("--base", default="http://localhost:8080", help="서비스 주소")
    parser.add_argument("--product", default="1", help="시험할 상품 ID")
    parser.add_argument("--users", type=int, default=100, help="동시에 구매할 사용자 수")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    before = get_product(base, args.product)
    print(f"상품: {before['name']}  상태: {before['status']}  시험 전 재고: {before['stock']}")
    if before["status"] == "UPCOMING":
        sys.exit("판매 전 상품입니다. 판매 중인 상품으로 시험하세요.")

    run_id = int(time.time())
    barrier = threading.Barrier(args.users)
    results = [None] * args.users

    def buyer(i):
        body = {"product_id": args.product, "nickname": f"tester{i}",
                "contact": f"load-{run_id}-{i}@test.local"}
        barrier.wait()   # 모든 스레드가 준비되면 동시에 요청
        results[i] = call("POST", f"{base}/api/orders", body)

    threads = [threading.Thread(target=buyer, args=(i,)) for i in range(args.users)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started

    counts = Counter()
    for status, body in results:
        counts["SUCCESS" if status == 201 else body.get("error", f"HTTP_{status}")] += 1
    success = counts["SUCCESS"]

    after = get_product(base, args.product)
    expected = min(args.users, before["stock"])

    print(f"\n동시 요청 {args.users}건, 소요 시간 {elapsed:.2f}초")
    for key, n in counts.most_common():
        print(f"  {key:<16} {n}건")
    print(f"\n시험 후 재고: {after['stock']}")
    print(f"기대: 성공 {expected}건, 재고 {before['stock'] - expected}")

    ok = success == expected and after["stock"] == before["stock"] - success
    print("\n결과: " + ("PASS - 초과 판매 없음" if ok else "FAIL - 결과를 확인하세요"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
