# DROP · 한정판 상품 선착순 판매 서비스

> 이 README는 작성 틀입니다. `TODO` 표시된 부분은 실제로 확인한 값으로 바꿔 주세요.

## 1. 서비스 소개

정해진 시각에 한정 수량을 선착순으로 판매하는 웹서비스입니다. 판매 시작 시각에 구매 요청이 한꺼번에 몰리는 상황에서도 재고보다 많이 팔리지 않도록(초과 판매 방지) 처리하는 것이 핵심입니다.

- **대상 사용자:** 한정판 상품(스니커즈, 굿즈 등)을 구매하려는 사용자
- **접속이 몰리는 기능:** 판매 시작 직후의 선착순 구매

### 사용자 기능

| 기능 | 설명 |
|---|---|
| 상품 조회 | 상품 목록, 가격, 남은 수량, 판매 상태(판매 중·오픈 예정·품절), 오픈까지 남은 시간 |
| 선착순 구매 | 닉네임·연락처를 입력해 구매. 1인 1개, 판매 시작 전·품절 시 구매 불가 |
| 주문 조회·취소 | 연락처로 내 주문 조회, 취소하면 재고 복구 |

## 2. AWS 구성도

```
사용자 브라우저
   │  http://<EC2 퍼블릭 IP>:8080
   ▼
EC2 (Ubuntu 24.04, 보안 그룹: TCP 8080 허용)
 └─ Docker Compose
     ├─ nginx :80  ── 화면(HTML·CSS·JS) 제공
     │     └─ /api/ 요청 전달
     └─ api (Flask + gunicorn) :5000
           └─ /data/data.json  (Docker 볼륨 drop_data)

상품 이미지: S3 버킷 (공개 읽기 s3:GetObject만 허용)
```

### 서비스 선택 이유

- **EC2:** 로컬에서 확인한 Docker Compose 구성을 그대로 실행할 수 있다.
- **S3:** 상품 이미지를 서버와 분리해 저장하고, URL로 바로 제공할 수 있다.
- TODO: 팀에서 판단한 내용을 추가

### 저장 방식

DB 대신 **메모리 + JSON 파일**을 사용합니다.

- 요청은 메모리에서 처리하고, 구매·취소처럼 데이터가 바뀔 때만 `data.json`에 저장합니다.
- 재고 확인 → 차감 → 주문 저장 → 파일 저장을 **하나의 Lock 안에서** 처리해 동시 요청에도 초과 판매가 없습니다.
- 임시 파일에 먼저 쓰고 `os.replace`로 교체해, 저장 도중 장애가 나도 파일이 깨지지 않습니다.
- 데이터가 프로세스 메모리에 있으므로 gunicorn 워커는 1개(`-w 1`)로 실행하고, 동시 요청은 스레드(`--threads 8`)로 처리합니다.

### ELB·Auto Scaling 검토

판매 시작 시각에 부하가 몰리므로 ELB + Auto Scaling 적용을 검토했으나, 이번에는 **단일 EC2**로 배포했습니다.

- 재고와 주문이 서버 메모리와 서버 안의 파일에 있어서, 서버를 여러 대로 늘리면 서버마다 재고가 따로 존재해 초과 판매가 발생한다. 확장하려면 Redis나 DB 같은 공유 저장소가 먼저 필요하다.
- Auto Scaling은 새 서버가 준비될 때까지 시간이 걸려, 몇 초 안에 끝나는 선착순 판매에는 반응이 늦다.
- 로드 밸런서와 추가 서버는 접속이 없을 때도 비용이 발생한다.

TODO: 팀의 최종 판단과 근거를 정리

## 3. 실행·배포

### 프로젝트 구조

```
compose.yaml          nginx와 api 서비스 정의
api/
  app.py              Flask API
  seed.json           최초 실행 시 사용하는 상품 데이터
  Dockerfile
  requirements.txt
nginx/
  nginx.conf          화면 제공, /api/ 요청을 Flask로 전달
  html/               index.html, style.css, app.js
  Dockerfile
tests/
  concurrent_buy.py   동시 구매 시험
```

### API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 상태 확인 |
| GET | `/api/products` | 상품 목록 |
| GET | `/api/products/<id>` | 상품 상세 |
| POST | `/api/orders` | 구매 `{product_id, nickname, contact}` |
| GET | `/api/orders?contact=...` | 내 주문 조회 |
| POST | `/api/orders/<id>/cancel` | 주문 취소 `{contact}` |

### 로컬 실행

```bash
docker compose config            # 설정 검사
docker compose up -d --build     # 실행
curl http://localhost:8080/api/health
```

브라우저에서 `http://localhost:8080` 접속

### EC2 배포

1. EC2 보안 그룹 인바운드 규칙에 **TCP 8080, 0.0.0.0/0** 추가 (SSH 22는 내 IP만 유지)
2. EC2에 접속해 Docker Compose 설치 확인

   ```bash
   docker compose version
   # 없으면
   sudo apt update
   sudo apt install -y docker-compose-v2
   ```

3. 프로젝트 내려받기와 실행

   ```bash
   git clone https://github.com/TODO/drop-shop.git
   cd drop-shop
   sudo docker compose up -d --build
   sudo docker compose ps
   curl http://localhost:8080/api/health
   ```

4. 브라우저에서 `http://<EC2 퍼블릭 IP>:8080` 접속

> EC2를 중지했다가 다시 시작하면 퍼블릭 IP가 바뀝니다. 콘솔에서 현재 IP를 다시 확인하세요.
> 컨테이너는 `restart: unless-stopped`로 설정되어 있어 EC2 재시작 후 자동으로 다시 실행됩니다.

### 상품 이미지(S3) 연결

1. S3 버킷에 상품 이미지 업로드 (9주차 실습과 같이 퍼블릭 액세스 차단 해제 + GetObject 버킷 정책)
2. 객체 URL을 `api/seed.json`의 `image_url`에 입력
3. `seed.json`은 `data.json`이 없을 때만 사용되므로 데이터를 초기화하고 다시 실행

   ```bash
   sudo docker compose down -v      # -v: 볼륨(data.json)까지 삭제
   sudo docker compose up -d --build
   ```

### 데이터 초기화

`docker compose down -v`로 볼륨을 지우면 `seed.json` 상태로 돌아갑니다. 동시 구매 시험을 다시 할 때 사용합니다. **시연 직전에는 실행하지 마세요.**

## 4. 검증

### 동시 구매 시험

```bash
python tests/concurrent_buy.py --base http://<EC2 퍼블릭 IP>:8080 --product 1 --users 100
```

재고 10개 상품에 100명이 동시에 구매 요청을 보내 정확히 10건만 성공하는지 확인합니다.

| 시험 환경 | 동시 요청 | 시험 전 재고 | 성공 | 품절 응답 | 시험 후 재고 | 결과 |
|---|---|---|---|---|---|---|
| TODO (로컬/EC2) | 100 | 10 | TODO | TODO | TODO | TODO |

### 기능별 시험 결과

배포 주소: `http://TODO:8080` / 시험 일시: TODO

| 기능 | 구분 | 입력 | 기대 결과 | 실제 결과 |
|---|---|---|---|---|
| 상품 조회 | 정상 | 메인 화면 접속 | 상품 목록, 재고, 상태 표시 | TODO |
| 상품 조회 | 경계 | 오픈 예정 상품 확인 | 카운트다운 표시, 구매 버튼 비활성 | TODO |
| 구매 | 정상 | 판매 중 상품, 닉네임·연락처 입력 | 주문번호 표시, 재고 1 감소 | TODO |
| 구매 | 실패 | 같은 연락처로 같은 상품 재구매 | "1인 1개까지만 구매" 메시지 | TODO |
| 구매 | 실패 | 재고 0인 상품 구매 | "품절되었습니다" 메시지 | TODO |
| 구매 | 실패 | 판매 시작 전 상품 구매 (API 직접 호출) | "아직 판매 전입니다" 메시지 | TODO |
| 주문 조회·취소 | 정상 | 연락처 입력 후 주문 취소 | 취소됨 표시, 재고 1 복구 | TODO |
| 주문 조회·취소 | 실패 | 이미 취소된 주문 재취소 (API 직접 호출) | "이미 취소된 주문" 메시지 | TODO |
| 데이터 유지 | 정상 | 구매 후 `docker compose restart` | 주문·재고 그대로 유지 | TODO |

## 5. 기록

### 팀 역할

| 이름 | 역할 |
|---|---|
| TODO | TODO |

### AI 활용

과제 규정에 따라 일반 AI(채팅형)만 사용했고, 에이전틱 코딩 도구는 사용하지 않았습니다.

| 사용 도구 | 활용 내용 | 직접 수정·검증한 내용 |
|---|---|---|
| Claude (claude.ai 채팅) | 주제 선정, 기능 설계, 초기 코드 작성 | TODO |

### 한계

- 서버 1대에서만 올바르게 동작한다(재고가 서버 메모리·파일에 있음).
- 구매할 때마다 파일을 쓰기 때문에 처리 속도에 한계가 있다.
- 실제 결제와 로그인 기능은 없다. 사용자는 연락처로 구분한다.

### 자원 정리

검사가 끝난 뒤 EC2 인스턴스(종료), 보안 그룹, S3 버킷을 삭제합니다. GitHub 저장소와 README는 보존합니다.
