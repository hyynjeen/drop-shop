// DROP 화면 동작
// - 상품 목록은 3초마다 다시 불러와 재고 변화를 보여준다.
// - 카운트다운은 서버 시각 기준으로 계산한다(사용자 PC 시계가 틀려도 정확하게).

const API = "/api";
const REFRESH_MS = 3000;
const MAX_SEGMENTS = 30;   // 재고가 이보다 많으면 칸 대신 막대로 표시

let products = [];
let serverOffset = 0;       // 서버 시각 - 내 PC 시각 (ms)
let selectedProduct = null;
let refreshTimer = null;

const $ = (sel) => document.querySelector(sel);

// ---------------------------------------------------------------- 공통
function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatPrice(n) {
  return `${Number(n).toLocaleString("ko-KR")}원`;
}

function formatTime(isoText) {
  return new Date(isoText).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "long", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function serverNow() {
  return Date.now() + serverOffset;
}

async function request(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = {};
  try { body = await res.json(); } catch { /* 본문이 JSON이 아닌 경우 */ }
  if (!res.ok) {
    const err = new Error(body.message || `요청에 실패했습니다. (HTTP ${res.status})`);
    err.code = body.error;
    throw err;
  }
  return body;
}

function showNotice(el, message, isError = false) {
  el.textContent = message;
  el.classList.toggle("is-error", isError);
  el.hidden = !message;
}

// ---------------------------------------------------------------- 탭
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("is-active", active);
    if (active) t.setAttribute("aria-current", "page");
    else t.removeAttribute("aria-current");
  });
  $("#products").hidden = name !== "products";
  $("#orders").hidden = name !== "orders";
  if (name === "products") {
    startRefresh();
  } else {
    stopRefresh();
    // 이 브라우저에서 구매한 적이 있으면 그 연락처로 바로 조회
    const input = $("#orders-contact");
    if (!input.value) input.value = localStorage.getItem("drop.contact") || "";
    if (input.value) loadOrders(input.value.trim());
  }
}

document.querySelectorAll("[data-tab]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    showTab(el.dataset.tab);
  });
});

// ---------------------------------------------------------------- 상품 목록
async function loadProducts() {
  try {
    const data = await request("/products");
    serverOffset = new Date(data.server_time).getTime() - Date.now();
    products = data.products;
    renderProducts();
    showNotice($("#products-status"), "");
  } catch (err) {
    showNotice($("#products-status"), `상품을 불러오지 못했습니다. ${err.message}`, true);
  }
}

function meterHtml(p) {
  const low = p.stock > 0 && p.stock <= Math.max(1, Math.ceil(p.total_stock * 0.2));
  if (p.total_stock <= MAX_SEGMENTS) {
    const cells = Array.from({ length: p.total_stock }, (_, i) =>
      `<span class="${i < p.stock ? "on" : ""}"></span>`).join("");
    return `<div class="meter ${low ? "is-low" : ""}" aria-hidden="true">${cells}</div>`;
  }
  const pct = p.total_stock ? (p.stock / p.total_stock) * 100 : 0;
  return `<div class="meter-bar" aria-hidden="true"><i style="width:${pct}%"></i></div>`;
}

function actionHtml(p) {
  if (p.status === "UPCOMING") {
    return `
      <div class="countdown">
        <span class="countdown-time" data-start="${escapeHtml(p.sale_start)}">--:--:--</span>
        <span class="countdown-label">${escapeHtml(formatTime(p.sale_start))} 오픈</span>
      </div>
      <button type="button" class="btn btn-primary" disabled>오픈 전</button>`;
  }
  if (p.status === "SOLD_OUT") {
    return `<button type="button" class="btn btn-primary" disabled>품절</button>`;
  }
  return `<button type="button" class="btn btn-primary" data-buy="${escapeHtml(p.id)}">구매하기</button>`;
}

const STATUS_LABEL = { ON_SALE: "판매 중", UPCOMING: "오픈 예정", SOLD_OUT: "품절" };

function renderProducts() {
  const list = $("#product-list");
  if (!products.length) {
    list.innerHTML = "";
    showNotice($("#products-status"), "등록된 상품이 없습니다.");
    return;
  }
  list.innerHTML = products.map((p) => `
    <li class="drop">
      <div class="drop-image">
        ${p.image_url
          ? `<img src="${escapeHtml(p.image_url)}" alt="${escapeHtml(p.name)}" loading="lazy">`
          : `<span>이미지 준비 중</span>`}
      </div>
      <div class="drop-info">
        <h2 class="drop-name">${escapeHtml(p.name)}</h2>
        <p class="drop-desc">${escapeHtml(p.description)}</p>
        <div class="drop-meta">
          <span class="price">${formatPrice(p.price)}</span>
          <span class="badge badge-${p.status}">${STATUS_LABEL[p.status]}</span>
        </div>
        <div class="stock">
          <div class="stock-label">
            <span>남은 수량 <strong>${p.stock}</strong> / ${p.total_stock}</span>
          </div>
          ${meterHtml(p)}
        </div>
      </div>
      <div class="drop-action">${actionHtml(p)}</div>
    </li>`).join("");
  tickCountdowns(false);
}

$("#product-list").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-buy]");
  if (btn) openBuyDialog(btn.dataset.buy);
});

let lastStartReload = 0;

function tickCountdowns(allowReload = true) {
  let reachedStart = false;
  document.querySelectorAll(".countdown-time").forEach((el) => {
    const left = new Date(el.dataset.start).getTime() - serverNow();
    if (left <= 0) {
      el.textContent = "00:00:00";
      reachedStart = true;
      return;
    }
    const s = Math.floor(left / 1000);
    const days = Math.floor(s / 86400);
    const hh = String(Math.floor((s % 86400) / 3600)).padStart(2, "0");
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    el.textContent = days > 0 ? `${days}일 ${hh}:${mm}:${ss}` : `${hh}:${mm}:${ss}`;
  });
  // 오픈 시각이 되면 바로 목록을 갱신해 구매 버튼을 연다 (1초에 한 번까지만)
  if (reachedStart && allowReload && Date.now() - lastStartReload > 1000) {
    lastStartReload = Date.now();
    loadProducts();
  }
}

function startRefresh() {
  stopRefresh();
  loadProducts();
  refreshTimer = setInterval(loadProducts, REFRESH_MS);
}

function stopRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = null;
}

setInterval(() => tickCountdowns(), 1000);

// ---------------------------------------------------------------- 구매
const buyDialog = $("#buy-dialog");
const buyResult = $("#buy-result");
const buySubmit = $("#buy-submit");

function openBuyDialog(productId) {
  selectedProduct = products.find((p) => p.id === productId);
  if (!selectedProduct) return;
  $("#buy-product").textContent = `${selectedProduct.name} · ${formatPrice(selectedProduct.price)}`;
  $("#buy-nickname").value = localStorage.getItem("drop.nickname") || "";
  $("#buy-contact").value = localStorage.getItem("drop.contact") || "";
  buyResult.hidden = true;
  buySubmit.hidden = false;
  buySubmit.disabled = false;
  buySubmit.textContent = "구매하기";
  $("#buy-cancel").textContent = "닫기";
  buyDialog.showModal();
}

$("#buy-cancel").addEventListener("click", () => buyDialog.close());

$("#buy-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedProduct) return;

  const nickname = $("#buy-nickname").value.trim();
  const contact = $("#buy-contact").value.trim();

  buySubmit.disabled = true;
  buySubmit.textContent = "구매 중…";
  try {
    const data = await request("/orders", {
      method: "POST",
      body: JSON.stringify({ product_id: selectedProduct.id, nickname, contact }),
    });
    localStorage.setItem("drop.nickname", nickname);
    localStorage.setItem("drop.contact", contact);
    buyResult.textContent = `구매했습니다. 주문번호 ${data.order.order_id}`;
    buyResult.className = "result is-ok";
    buySubmit.hidden = true;
    $("#buy-cancel").textContent = "확인";
  } catch (err) {
    buyResult.textContent = err.message;
    buyResult.className = "result is-error";
    buySubmit.disabled = false;
    buySubmit.textContent = "다시 시도";
  }
  buyResult.hidden = false;
  loadProducts();
});

// ---------------------------------------------------------------- 내 주문
async function loadOrders(contact) {
  const status = $("#orders-status");
  try {
    const data = await request(`/orders?contact=${encodeURIComponent(contact)}`);
    renderOrders(data.orders);
    showNotice(status, data.orders.length ? "" : "이 연락처로 주문한 내역이 없습니다.");
  } catch (err) {
    $("#order-list").innerHTML = "";
    showNotice(status, err.message, true);
  }
}

function renderOrders(orders) {
  $("#order-list").innerHTML = orders.map((o) => {
    const canceled = o.status === "CANCELED";
    return `
      <li class="order ${canceled ? "is-canceled" : ""}">
        <div>
          <p class="order-name">${escapeHtml(o.product_name)}</p>
          <p class="order-meta">
            주문번호 ${escapeHtml(o.order_id)} · ${escapeHtml(o.nickname)} · ${escapeHtml(formatTime(o.created_at))} 주문
            ${canceled ? ` · ${escapeHtml(formatTime(o.canceled_at))} 취소됨` : ""}
          </p>
        </div>
        ${canceled ? "" : `<button type="button" class="btn btn-danger" data-cancel="${escapeHtml(o.order_id)}">주문 취소</button>`}
      </li>`;
  }).join("");
}

$("#orders-form").addEventListener("submit", (e) => {
  e.preventDefault();
  loadOrders($("#orders-contact").value.trim());
});

$("#order-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-cancel]");
  if (!btn) return;
  if (!confirm("이 주문을 취소할까요? 취소한 재고는 다른 사람이 구매할 수 있습니다.")) return;

  const contact = $("#orders-contact").value.trim();
  btn.disabled = true;
  try {
    await request(`/orders/${encodeURIComponent(btn.dataset.cancel)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ contact }),
    });
    await loadOrders(contact);
    showNotice($("#orders-status"), `주문번호 ${btn.dataset.cancel}을(를) 취소했습니다.`);
  } catch (err) {
    btn.disabled = false;
    showNotice($("#orders-status"), err.message, true);
  }
});

// ---------------------------------------------------------------- 시작
$("#orders-contact").value = localStorage.getItem("drop.contact") || "";
startRefresh();
