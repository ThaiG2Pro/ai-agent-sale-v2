# Plan v4: 4.7 → CHẠY THẬT trên production (CD + edge + ops)

## Context

Plan V3 đóng đủ 6/6 WP (V3-0 CI · V3-1 coverage gate · V3-2 OTel per-node · V3-3 Tier-F graph-path
12/12×3 + nightly workflow · V3-4 clarify borderline intents COMPLETED 2026-08-05 · V3-5 dọn nợ).
Scorecard ~4.8/5; phần còn thiếu tới 5.0 được nêu đích danh trong Verification tổng V3: **deploy pipeline thật** — và khi soi kỹ,
"thật" nghĩa là cả một lớp ops chưa tồn tại:

**Khoảng cách đo được tới prod (khảo sát 2026-08-05):**

1. **Không có CD** — image không build/push đi đâu; "deploy" = `docker compose up` tay trên máy dev.
2. **Không có edge layer** — không reverse proxy, không TLS, không domain; Telegram webhook prod
   bắt buộc HTTPS (hiện dùng tunnel dev `scripts/setup_telegram_tunnel.sh`). Rate limiting = 0
   (ghi chú "Week 7" trong `docs/deployment.md` chưa bao giờ làm).
3. **Compose hiện tại là compose DEV**: publish `5432` (Postgres) và `6006/4317/4318` (Phoenix —
   **không có auth**) ra mọi interface. Đưa nguyên file này lên VPS = mở DB + toàn bộ trace
   (chứa nội dung hội thoại khách) cho cả internet.
4. **Không có backup** — không `pg_dump`, không restore drill. Mất volume = mất catalog + memory +
   checkpoint + HITL queue, không đường về.
5. **Không có alerting** — healthcheck có nhưng không ai được báo; api chết lúc 2h sáng thì sáng
   ra mới biết. Phoenix là viewer, không phải alerter.
6. **Migration/rollback lúc deploy** — `alembic upgrade head` chạy tay; chưa có quy trình
   image-tag rollback.
7. **Chưa có số capacity** — suite `-m performance` bị loại khỏi CI (flaky trên máy yếu), chưa đo
   concurrent users chịu được / hành vi khi Groq rate-limit (free tier ~30 req/min).
8. **Security scan chưa có trong CI** — R-SEC A06 nhắc Trivy; chưa có Trivy image scan lẫn
   `pip-audit`.

**Nguyên tắc xuyên suốt (kế thừa V2/V3):**
- Zero-Cost-First áp cho cả hạ tầng: mục tiêu là **1 VPS nhỏ (~$5-10/tháng) + GitHub Actions
  free tier + Caddy (TLS free)** — không Kubernetes, không managed DB, không SaaS trả phí.
- Mọi WP hạ tầng fast-track được (không đổi hành vi agent — không cần sdlc-full, trừ V4-2 phần
  rate-limit trong app nếu chọn làm ở tầng app).
- Mỗi WP kết thúc bằng commit riêng theo `R-GIT-001`; secrets CHỈ qua GitHub Secrets / env trên
  VPS — không bao giờ nằm trong repo (R-SEC-001).
- **Máy dev yếu**: mọi thứ nặng (build image, load test) chạy trên CI runner hoặc VPS, không
  chạy local.

**Thứ tự & phụ thuộc:** V4-0 (quyết định hạ tầng + ADR — **cần quyết định từ user**) mở đường cho
tất cả. V4-1 (CD) và V4-6 (security scan trong CI) độc lập nhau, làm sớm. V4-2 (edge/TLS/webhook)
cần VPS từ V4-0 + image từ V4-1. V4-3 (DB ops) và V4-4 (alerting) cần VPS đang chạy. V4-5 (load
test) làm CUỐI — đo trên staging thật mới có ý nghĩa.

**Baseline (main @ `9884271`):** CI xanh (lint + unit + coverage + Tier-R) · Tier-F 12/12×3
local · nightly-eval.yml đã có nhưng **chưa chạy lần nào trên GitHub** (thiếu `GROQ_API_KEY`
secret — việc tay #1 của V4-0) · chat trên Groq, embeddings fastembed in-process.

---

## WP-V4-0 — Quyết định hạ tầng + staging VPS (làm trước, cần user)

Chốt hạ tầng bằng **ADR-007** rồi dựng staging. Đề xuất mặc định (đúng Zero-Cost-First, đúng
quy mô SME single-tenant):

- **1 VPS** (Hetzner CX22 / DigitalOcean / VN provider — user chọn & mua): 2 vCPU, 4GB RAM đủ
  cho api (4 uvicorn worker) + Postgres + Caddy; fastembed ONNX chạy CPU thoải mái.
- **Topology**: docker compose trên VPS (không k8s) — thêm `docker-compose.prod.yml` override:
  KHÔNG publish `5432`; Phoenix bind `127.0.0.1` hoặc chặn sau Caddy basic-auth; api chỉ expose
  qua Caddy.
- **Môi trường**: 1 máy = staging trước; khi ổn thì chính nó thăng cấp prod (SME một khách),
  hoặc nhân đôi nếu cần tách. Ghi rõ lựa chọn trong ADR.

Việc tay của user (checklist trong ADR):
1. Thêm `GROQ_API_KEY` vào GitHub Secrets + dispatch "Nightly Eval (Tier-F)" 1 lần → xanh
   (đóng loose-end V3-3).
2. Mua VPS + domain (hoặc subdomain miễn phí DuckDNS cho staging); đưa SSH key deploy vào
   GitHub Secrets.
3. Tạo bot Telegram riêng cho staging (không dùng chung bot dev).

**Done:** ADR-007 committed; VPS SSH được; `docker compose -f docker-compose.yml -f
docker-compose.prod.yml config` hợp lệ và không publish port thừa.
Commit `docs(adr): ADR-007 production topology — single-VPS compose + Caddy edge`.

---

## WP-V4-1 — CD pipeline: build → GHCR → deploy staging

1. **Workflow `release.yml`**: push tag `v*` (hoặc merge vào `main` với path filter) →
   build image (`docker/build-push-action`, cache GHA) → push `ghcr.io/<owner>/ai-agent-sale-v2:
   {sha,tag,latest}`.
2. **Job deploy** (environment `staging`, cần approve trên GitHub UI lần đầu): SSH vào VPS →
   `docker compose pull && docker compose up -d` → chờ `/health/readiness` 200 → nếu fail,
   tự `docker compose up -d` lại image tag trước (rollback = re-pin tag, ghi trong runbook).
3. **Migration trong deploy**: chạy `alembic upgrade head` như một bước one-shot
   (`docker compose run --rm api alembic upgrade head`) TRƯỚC khi swap container — thứ tự
   migrate-then-deploy, và mọi migration phải backward-compatible 1 phiên bản (R-DB-001 đã yêu
   cầu rollback path).
4. Image được scan bởi V4-6 trước khi push (nếu V4-6 xong trước thì gắn luôn vào job build).

**Done:** push tag → 5-10 phút sau staging chạy image mới, readiness xanh; demo rollback bằng
re-deploy tag cũ.
Commit `ci(cd): <ticket> build+push GHCR, SSH compose deploy with readiness gate`.

---

## WP-V4-2 — Edge layer: Caddy + TLS + Telegram webhook thật + rate limit

1. **Caddy** thêm vào compose prod: reverse proxy `https://<domain>` → `api:8000`; TLS tự động
   (Let's Encrypt). Phoenix sau `https://<domain>/phoenix` với basic-auth (hash trong env VPS),
   hoặc chỉ bind localhost + xem qua SSH tunnel — chọn 1, ghi vào ADR-007.
2. **Telegram webhook prod**: `TELEGRAM_WEBHOOK_URL=https://<domain>/webhooks/telegram` +
   secret ≥20 chars mới cho staging bot; script `scripts/set_webhook.py` (gọi `setWebhook`,
   idempotent) thay cho tunnel script ở prod.
3. **Rate limiting** (đóng nợ "Week 7"): tầng Caddy per-IP cho `/query` + `/webhooks/telegram`
   (429 sớm, giữ ack path nhanh — đúng khuyến nghị có sẵn trong `docs/deployment.md`). Tầng app
   KHÔNG làm ở V4 trừ khi đo thấy cần (tránh đụng answer path — nếu cần thì đi sdlc-full).
4. **Siết compose prod**: bỏ publish `5432`; bỏ publish `4317/4318` (OTLP chỉ nội bộ network);
   api không publish trực tiếp — chỉ Caddy có port 80/443.

**Done:** `curl https://<domain>/health/readiness` 200 với cert hợp lệ; nhắn bot staging trên
Telegram → trả lời qua webhook (không tunnel); vượt ngưỡng rate → 429; `nmap` từ ngoài chỉ thấy
22/80/443.
Commit `feat(infra): <ticket> Caddy edge — TLS, phoenix auth, per-IP rate limit`.

---

## WP-V4-3 — DB ops: backup, restore drill, retention

1. **Backup hằng đêm**: cron trên VPS (hoặc sidecar container) chạy `pg_dump -Fc` → giữ 7 daily
   + 4 weekly; copy off-site (rclone → object storage free tier / thậm chí GitHub artifact
   private cho staging — chọn 1, chi phí ~0).
2. **Restore drill BẮT BUỘC**: script `scripts/restore_check.sh` — restore dump mới nhất vào DB
   tạm, đếm rows các bảng chính (`products`, `text_embeddings`, `semantic_memory`, checkpoint) so
   với nguồn. Backup chưa restore thử = chưa có backup.
3. **Retention/vacuum**: Phoenix SQLite volume lớn dần — đặt `PHOENIX_...` retention hoặc cron
   dọn; nightly HITL archiving đã có sẵn (giữ nguyên).
4. Runbook `docs/ops-runbook.md` §DB: backup ở đâu, restore thế nào, mất bao lâu (đo RTO thật).

**Done:** dump đêm tự chạy + off-site; restore drill pass và được ghi số (size, thời gian);
runbook §DB xong.
Commit `feat(ops): <ticket> nightly pg_dump + off-site + restore drill script`.

---

## WP-V4-4 — Alerting & vận hành: biết khi nó chết

1. **Uptime alert**: healthchecks.io / UptimeRobot free ping `GET /health/readiness` mỗi 1-5
   phút → báo qua **Telegram** (đằng nào cũng đã có bot ops). Zero-cost, không tự host.
2. **Error-rate alert từ trong app**: handler 500 toàn cục đã có — thêm notifier throttled
   (≤1 msg/5 phút) bắn Telegram ops chat khi 500 xuất hiện; kill-switch `OPS_ALERT_ENABLED`.
   KHÔNG gửi nội dung request (R-SEC-002) — chỉ route + exception class + request_id.
3. **Log**: uvicorn/app log ra json (đã structured) → `docker logs` với `max-size`/`max-file`
   rotation trong compose prod (tránh đầy disk — kẻ giết VPS số 1).
4. **Cost guard nhìn được**: `/admin/costs` đã có — thêm mục kiểm tra hằng tuần vào runbook
   (Groq usage vs free tier limit) thay vì xây dashboard mới.

**Done:** tắt api container → Telegram ops nhận alert < 5 phút; ném exception thử → nhận alert
500 có request_id; log rotation kích hoạt được.
Commit `feat(ops): <ticket> uptime + throttled 500 alerts to ops Telegram, log rotation`.

---

## WP-V4-5 — Capacity baseline & graceful degradation (làm cuối, trên staging)

1. **Load test trên staging** (không phải máy dev): k6/locust bắn `/query` — tìm p95 latency và
   điểm gãy theo concurrent users với 4 uvicorn worker + Groq. Ghi số vào
   `docs/ops-runbook.md` §Capacity ("chịu được N user đồng thời, p95 X s").
2. **Groq rate-limit behavior**: ép 429 từ Groq (hạ limit / bắn dồn) — xác nhận LiteLLM retry
   hoặc fail-fast rõ ràng, user nhận thông điệp "shop đang bận" thay vì treo/stack trace. Nếu
   phải sửa answer path → tách CR đi sdlc-full.
3. **Suite `-m performance`**: chạy được TRÊN VPS/staging như một job manual (workflow_dispatch,
   chạy trên self-hosted hoặc SSH), thoát kiếp "flaky trên máy dev yếu" — không cắm vào CI push.

**Done:** số capacity ghi trong runbook; kịch bản Groq-429 cho ra thông điệp thân thiện; perf
suite chạy pass trên staging ít nhất 1 lần.
Commit `test(perf): <ticket> staging load baseline + provider-429 degradation`.

---

## WP-V4-6 — Security gate trong CI: Trivy + pip-audit (fast-track, làm sớm được)

1. **Trivy image scan** trong `release.yml` (sau build, trước push): fail khi CRITICAL/HIGH có
   fix — đóng đúng lời hứa R-SEC A06 trong rules-registry.
2. **`pip-audit`** (hoặc `uv`-native audit khi có) chạy trong ci.yml job lint — cảnh báo
   dependency CVE; hai cái đang treo sẵn (`grpcio` yanked, `typer` extra warning) xử lý luôn.
3. **Secret scan**: bật GitHub secret scanning + push protection trên repo (việc tay, 2 phút).
4. Rà lại `.env.example` khớp toàn bộ biến mới của V4 (OPS_ALERT_ENABLED, domain, …).

**Done:** CI đỏ khi image có CRITICAL CVE có fix; pip-audit chạy mỗi PR; push protection bật.
Commit `ci(security): <ticket> trivy image gate + pip-audit`.

---

## Verification tổng V4 (sau khi mọi WP xong)

1. **Deploy chuỗi đầy đủ**: sửa 1 dòng → PR → CI xanh → merge → tag → image lên GHCR → staging
   tự cập nhật → readiness xanh → rollback thử về tag trước thành công.
2. **Khách thật đi qua edge**: nhắn bot Telegram staging từ điện thoại → trả lời đúng, có
   citation; trace turn đó thấy trong Phoenix (qua đường có auth).
3. **Kill test**: tắt api container → alert Telegram < 5 phút; bật lại → tự phục hồi
   (`restart: unless-stopped`).
4. **Restore drill**: xoá DB staging (có chủ đích) → restore từ backup đêm → bot trả lời lại
   bình thường, đếm rows khớp.
5. **Port scan từ ngoài**: chỉ 22/80/443 mở; Phoenix và Postgres không chạm được từ internet.
6. **Số capacity** ghi trong runbook + kịch bản Groq-429 degradation PASS.
7. Re-score `docs/feature-scorecard.md`: kỳ vọng 001 (Infra) 4.3 → ~4.8; toàn dự án tiệm cận
   **~4.9/5**. Phần còn lại tới 5.0 tuyệt đối sau V4: WP-V3-4 (clarify) quay lại + multi-tenant
   thật sự (ngoài scope — sản phẩm hoá).

## Giao việc

V4-0 trước (chờ user mua VPS/domain — trong lúc chờ làm V4-6 và V4-1 phần build/push được ngay,
chỉ job deploy cần VPS). Sau đó V4-2 → V4-3 ∥ V4-4 → V4-5 cuối. Tất cả fast-track trừ khi V4-2
(rate-limit tầng app) hoặc V4-5 (degradation sửa answer path) phát sinh thay đổi hành vi agent —
khi đó tách CR chạy `sdlc-full`. Khi giao kèm: nội dung WP nguyên văn + ADR-007 + số baseline
(CI hiện tại, Tier-F 12/12, compose dev hiện trạng).
