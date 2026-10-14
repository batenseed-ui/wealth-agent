(function () {
  "use strict";

  // ---------- small helpers ----------
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    });
    (children || []).forEach((c) => node.append(c));
    return node;
  }

  const store = {
    get(key) {
      try { return window.localStorage.getItem(key); } catch (_) { return null; }
    },
    set(key, value) {
      try { window.localStorage.setItem(key, value); } catch (_) { /* storage may be blocked */ }
    },
    remove(key) {
      try { window.localStorage.removeItem(key); } catch (_) { /* ignore */ }
    },
  };

  // ---------- input persistence ----------
  function formValues(form) {
    const out = {};
    $$("input, select", form).forEach((f) => {
      if (!f.name) return;
      out[f.name] = f.type === "checkbox" ? f.checked : f.value;
    });
    return out;
  }

  function persistForm(form) {
    const key = "wa:" + form.dataset.persist;
    const raw = store.get(key);
    if (raw) {
      try {
        const saved = JSON.parse(raw);
        $$("input, select", form).forEach((f) => {
          if (!f.name || !(f.name in saved)) return;
          if (f.type === "checkbox") f.checked = !!saved[f.name];
          else f.value = saved[f.name];
        });
      } catch (_) { /* ignore corrupt data */ }
    }
    const save = () => store.set(key, JSON.stringify(formValues(form)));
    form.addEventListener("input", save);
    form.addEventListener("change", save);
  }

  // ---------- currency ----------
  const currencySel = $("#currency");
  const savedCurrency = store.get("wa:currency");
  if (savedCurrency && $$("option", currencySel).some((o) => o.value === savedCurrency)) currencySel.value = savedCurrency;
  currencySel.addEventListener("change", () => store.set("wa:currency", currencySel.value));

  function money(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "n/a";
    const abs = Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: digits || 0, minimumFractionDigits: digits || 0 });
    return (n < 0 ? "-" : "") + currencySel.value + abs;
  }

  // ---------- banner ----------
  const banner = $("#banner");
  const bannerText = $("#banner-text");
  const bannerRetry = $("#banner-retry");
  let retryFn = null;

  function showBanner(message, retry) {
    bannerText.textContent = message;
    retryFn = retry || null;
    bannerRetry.hidden = !retry;
    banner.hidden = false;
  }
  function hideBanner() { banner.hidden = true; retryFn = null; }
  $("#banner-close").addEventListener("click", hideBanner);
  bannerRetry.addEventListener("click", () => { const fn = retryFn; hideBanner(); if (fn) fn(); });

  // ---------- API wrapper ----------
  class ApiError extends Error {
    constructor(kind, message, fieldErrors) {
      super(message);
      this.kind = kind;
      this.fieldErrors = fieldErrors || null;
    }
  }

  async function api(path, options) {
    const opts = options || {};
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeout || 20000);
    let response;
    try {
      response = await fetch(path, {
        method: opts.method || "GET",
        headers: opts.body ? { "Content-Type": "application/json" } : undefined,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw new ApiError("timeout", "The server took too long to respond. Your inputs are saved; please try again.");
      }
      throw new ApiError("network", "Could not reach the server. Check that the Flask app is running and your connection is up. Your inputs are saved.");
    } finally {
      clearTimeout(timer);
    }

    let data = null;
    try { data = await response.json(); } catch (_) { /* handled below */ }

    if (!data || typeof data !== "object") {
      throw new ApiError("badresponse", `The server sent an unexpected response (HTTP ${response.status}). Please try again.`);
    }
    if (!response.ok || data.ok === false) {
      if (response.status === 400 && data.field_errors) {
        throw new ApiError("validation", data.error || "Please fix the highlighted fields.", data.field_errors);
      }
      throw new ApiError("server", data.error || `The server returned HTTP ${response.status}.`);
    }
    return data;
  }

  // ---------- field errors ----------
  function clearFieldErrors(scope) {
    $$("[data-error-for]", scope).forEach((p) => { p.textContent = ""; });
    $$("[aria-invalid]", scope).forEach((i) => i.removeAttribute("aria-invalid"));
  }
  function showFieldErrors(scope, errors) {
    let first = null;
    Object.entries(errors).forEach(([name, message]) => {
      const p = $(`[data-error-for="${name}"]`, scope);
      const input = $(`[name="${name}"]`, scope);
      if (p) p.textContent = message;
      if (input) {
        input.setAttribute("aria-invalid", "true");
        if (!first) first = input;
      }
    });
    if (first) first.focus();
  }

  // ---------- busy button helper ----------
  async function withBusy(button, busyLabel, fn) {
    const original = button.textContent;
    const siblings = $$(".actions .btn", button.closest("form") || document);
    siblings.forEach((b) => { b.disabled = true; });
    button.textContent = busyLabel;
    try {
      await fn();
    } finally {
      button.textContent = original;
      siblings.forEach((b) => { b.disabled = false; });
    }
  }

  // ---------- profile form ----------
  const profileForm = $("#profile-form");
  persistForm(profileForm);

  function readProfile() {
    const v = formValues(profileForm);
    delete v.currency;
    return v;
  }

  const CIRC = 2 * Math.PI * 52;
  const ring = $("#ring-fg");
  ring.style.strokeDasharray = String(CIRC);
  ring.style.strokeDashoffset = String(CIRC);

  function gradeColor(score) {
    if (score >= 65) return "var(--good)";
    if (score >= 50) return "var(--ok)";
    return "var(--poor)";
  }

  function renderScore(analysis, guidance) {
    $("#empty-state").hidden = true;
    $("#score-card").hidden = false;

    $("#score-num").textContent = String(analysis.score);
    ring.style.stroke = gradeColor(analysis.score);
    ring.style.strokeDashoffset = String(CIRC * (1 - analysis.score / 100));
    $("#score-grade").textContent = analysis.grade;
    $("#score-summary").textContent = guidance.summary;

    const body = $("#breakdown-body");
    body.textContent = "";
    analysis.components.forEach((c) => {
      const bar = el("div", { class: "bar" }, [el("span", { class: "st-" + c.status })]);
      bar.firstChild.style.width = Math.round((c.points / c.max) * 100) + "%";
      const nameCell = el("td", {}, [
        el("strong", { text: c.name }),
        el("span", { class: "detail", text: c.detail }),
      ]);
      const ptsCell = el("td", { text: `${c.points} / ${c.max}` });
      const barCell = el("td", { class: "bar-col" }, [bar]);
      body.append(el("tr", {}, [nameCell, ptsCell, barCell]));
    });

    const g = $("#guidance");
    g.textContent = "";
    g.append(el("h3", { text: "Where to focus first" }));
    if (guidance.recommendations.length) {
      const ol = el("ol");
      guidance.recommendations.forEach((r) => {
        ol.append(el("li", {}, [
          el("strong", { text: r.title + ". " }),
          document.createTextNode(r.action + ` (up to ${r.points_available} points available)`),
        ]));
      });
      g.append(ol);
    } else {
      g.append(el("p", { class: "muted", text: "Every area is on target." }));
    }
    g.append(el("p", { class: "fine", text: guidance.risk_note }));
  }

  function handleError(err, retry, scope) {
    if (err instanceof ApiError && err.kind === "validation" && err.fieldErrors) {
      showFieldErrors(scope, err.fieldErrors);
      showBanner(err.message);
    } else if (err instanceof ApiError) {
      showBanner(err.message, retry);
    } else {
      showBanner("Something went wrong in the page. Your inputs are saved; please try again.", retry);
      console.error(err);
    }
  }

  async function analyze() {
    hideBanner();
    clearFieldErrors(profileForm);
    await withBusy($("#btn-analyze"), "Analyzing…", async () => {
      try {
        const data = await api("/api/analyze", { method: "POST", body: readProfile() });
        renderScore(data.analysis, data.guidance);
      } catch (err) {
        handleError(err, analyze, profileForm);
      }
    });
  }

  async function getAdvice() {
    hideBanner();
    clearFieldErrors(profileForm);
    await withBusy($("#btn-advice"), "Getting advice…", async () => {
      try {
        const data = await api("/api/advice", { method: "POST", body: readProfile(), timeout: 45000 });
        renderScore(data.analysis, data.guidance);
        const card = $("#advice-card");
        card.hidden = false;
        const badge = $("#advice-badge");
        const notice = $("#advice-notice");
        const text = $("#advice-text");
        if (data.source !== "rule-based") {
          badge.textContent = data.source === "gemini" ? "AI advice (Gemini)" : "AI advice (Claude)";
          notice.hidden = true;
          text.textContent = data.advice_text;
        } else {
          badge.textContent = "Rule-based fallback";
          notice.hidden = false;
          notice.textContent = data.notice || "AI advice is unavailable right now.";
          text.textContent = data.guidance.summary + "\n\n" +
            "The prioritised actions, with amounts worked out from your numbers, are listed under your score above.\n\n" +
            data.guidance.risk_note;
        }
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (err) {
        handleError(err, getAdvice, profileForm);
      }
    });
  }

  $("#btn-analyze").addEventListener("click", analyze);
  $("#btn-advice").addEventListener("click", getAdvice);
  profileForm.addEventListener("submit", (e) => { e.preventDefault(); analyze(); });

  const SAMPLE = {
    age: "34", retirement_age: "62", monthly_income: "5500", monthly_expenses: "3200",
    monthly_debt_payments: "600", monthly_investment: "400", liquid_savings: "9000",
    investments: "42000", total_debt: "38000", risk_tolerance: "medium",
    has_health_insurance: true, has_life_insurance: false,
  };
  $("#btn-sample").addEventListener("click", () => {
    $$("input, select", profileForm).forEach((f) => {
      if (!(f.name in SAMPLE)) return;
      if (f.type === "checkbox") f.checked = SAMPLE[f.name]; else f.value = SAMPLE[f.name];
    });
    profileForm.dispatchEvent(new Event("input", { bubbles: true }));
    clearFieldErrors(profileForm);
  });
  $("#btn-clear").addEventListener("click", () => {
    store.remove("wa:profile");
    profileForm.reset();
    clearFieldErrors(profileForm);
  });

  // ---------- calculators ----------
  const CALC_RENDER = {
    compound(r) {
      const dl = rows([
        ["Final balance", money(r.final_balance)],
        ["You contributed", money(r.total_contributed)],
        ["Growth earned", money(r.interest_earned)],
      ]);
      const table = el("table", {}, [
        el("thead", {}, [el("tr", {}, [el("th", { text: "Year" }), el("th", { text: "Balance" })])]),
        el("tbody", {}, r.schedule.map((s) => el("tr", {}, [el("td", { text: String(s.year) }), el("td", { text: money(s.balance) })]))),
      ]);
      return [dl, el("details", {}, [el("summary", { text: "Year by year" }), table])];
    },
    loan(r) {
      return [rows([
        ["Monthly payment", money(r.monthly_payment, 2)],
        ["Total paid", money(r.total_paid)],
        ["Total interest", money(r.total_interest)],
        ["Number of payments", String(r.months)],
      ])];
    },
    goal(r) {
      if (!r.reached) {
        return [el("p", { text: `Not reached within 100 years at this pace (balance ${money(r.final_balance)}). Try saving more each month.` })];
      }
      if (r.months === 0) return [el("p", { text: "You have already reached this goal." })];
      return [rows([
        ["Time to goal", `${r.years} years (${r.months} months)`],
        ["Balance at that point", money(r.final_balance)],
      ])];
    },
  };

  function rows(pairs) {
    const dl = el("dl");
    pairs.forEach(([k, v]) => { dl.append(el("dt", { text: k }), el("dd", { text: v })); });
    return dl;
  }

  $$("form.calc").forEach((form) => {
    persistForm(form);
    const kind = form.dataset.calc;
    const out = $(".calc-out", form);
    const submit = $("button[type=submit]", form);

    async function run() {
      hideBanner();
      clearFieldErrors(form);
      out.textContent = "";
      const original = submit.textContent;
      submit.disabled = true;
      submit.textContent = "Calculating…";
      try {
        const data = await api("/api/calc/" + kind, { method: "POST", body: formValues(form) });
        CALC_RENDER[kind](data.result).forEach((n) => out.append(n));
      } catch (err) {
        handleError(err, run, form);
      } finally {
        submit.disabled = false;
        submit.textContent = original;
      }
    }
    form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
  });

  // ---------- methodology ----------
  async function loadMethodology() {
    const body = $("#method-body");
    try {
      const { methodology: m } = await api("/api/methodology");
      body.textContent = "";
      body.append(el("p", { text: m.summary }));
      const ul = el("ul");
      m.components.forEach((c) => {
        ul.append(el("li", {}, [
          el("strong", { text: `${c.name} (${c.max} points)` }),
          el("br"),
          el("span", { class: "formula", text: c.formula }),
          el("br"),
          el("span", { class: "muted", text: "Target: " + c.target }),
        ]));
      });
      body.append(ul);
      body.append(el("p", { text: "Assumptions: " + Object.values(m.assumptions).join("; ") + "." }));
      body.append(el("p", { text: "Grades: " + m.grades.map((g) => `${g.label} (${g.min}+)`).join(", ") + "." }));
      body.append(el("p", { class: "fine", text: m.limits }));
    } catch (err) {
      body.textContent = "";
      const retry = el("button", { type: "button", class: "link-btn", text: "Try again" });
      retry.addEventListener("click", loadMethodology);
      body.append(el("p", { class: "muted", text: "The scoring method could not be loaded from the server. " }), retry);
    }
  }
  loadMethodology();
})();
