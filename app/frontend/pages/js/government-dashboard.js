(function () {
  const el = (id) => document.getElementById(id);
  const state = {
    selectedState: "",
    selectedDistrict: "",
    states: [],
    currentStatePayload: null,
    currentDistrictPayload: null,
  };

  function log(v) {
    el("status").textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2);
    console.log("[GovernmentDashboard]", v);
  }

  async function parse(res) {
    const txt = await res.text();
    let data = {};
    try { data = txt ? JSON.parse(txt) : {}; } catch { data = { detail: txt || ("HTTP " + res.status) }; }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : (txt || ("HTTP " + res.status)));
    return data;
  }

  function qs() {
    const q = new URLSearchParams();
    if (el("filterDisease").value) q.set("disease_category", el("filterDisease").value);
    q.set("days", el("filterDays").value);
    if (el("filterGender").value) q.set("gender", el("filterGender").value);
    return q.toString();
  }

  async function loadStates() {
    const data = await parse(await fetch("/api/government/states?" + qs()));
    state.states = data.states || [];
    applyHeatmapToSvg();
    if (!state.selectedState && state.states.length) {
      state.selectedState = state.states[0].state;
      el("selectedState").textContent = state.selectedState;
    }
    if (state.selectedState) {
      await loadState(state.selectedState);
    } else {
      log("No state data available.");
    }
  }

  function applyHeatmapToSvg() {
    const mapObject = el("indiaSvgObject");
    const doc = mapObject.contentDocument;
    if (!doc) return;
    const max = Math.max(...state.states.map((s) => Number(s.risk_score || 0)), 1);
    const byState = {};
    state.states.forEach((s) => { byState[s.state] = Number(s.risk_score || 0); });

    doc.querySelectorAll("[data-state]").forEach((node) => {
      const stateName = node.getAttribute("data-state");
      const risk = byState[stateName] || 0;
      const t = Math.min(0.95, 0.15 + (risk / max) * 0.8);
      node.style.fill = `rgba(30,93,168,${t.toFixed(3)})`;
      node.style.stroke = stateName === state.selectedState ? "#0f3f7c" : "#8fb0d9";
      node.style.strokeWidth = stateName === state.selectedState ? "4" : "2";
      node.onclick = () => {
        state.selectedState = stateName;
        state.selectedDistrict = "";
        el("selectedState").textContent = state.selectedState;
        applyHeatmapToSvg();
        loadState(stateName).catch((e) => log(e.message || String(e)));
      };
    });
  }

  function renderDistrictList(districts) {
    const list = el("districtList");
    list.innerHTML = "";
    if (!districts.length) {
      list.innerHTML = '<div class="district-item">No districts available</div>';
      return;
    }
    if (!state.selectedDistrict) state.selectedDistrict = districts[0].district;
    districts.forEach((d) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "district-item" + (d.district === state.selectedDistrict ? " active" : "");
      row.textContent = `${d.district} (risk ${d.risk_score})`;
      row.onclick = () => {
        state.selectedDistrict = d.district;
        renderDistrictList(districts);
        loadDistrict(state.selectedDistrict).catch((e) => log(e.message || String(e)));
      };
      list.appendChild(row);
    });
  }

  function drawTrend(rows) {
    const canvas = el("trendChart");
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = "#d7e5f5";
    for (let i = 0; i <= 6; i += 1) {
      const y = 20 + i * 45;
      ctx.beginPath();
      ctx.moveTo(44, y);
      ctx.lineTo(w - 18, y);
      ctx.stroke();
    }

    const grouped = {};
    rows.forEach((r, idx) => {
      const cat = Number(r.disease_category || 0);
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(Math.max(0, Math.round(Number(r.percentage_change || 0))));
    });
    const colors = ["#3B82F6","#22C55E","#F59E0B","#EF4444","#8B5CF6","#06B6D4","#84CC16","#F97316","#EC4899","#64748B","#10B981","#EAB308"];
    const cats = Object.keys(grouped).map(Number).sort((a, b) => a - b).slice(0, 8);
    const allValues = cats.flatMap((cat) => grouped[cat] || []);
    const globalMax = Math.max(1, ...allValues);
    cats.forEach((cat) => {
      const arr = grouped[cat];
      ctx.strokeStyle = colors[(cat - 1 + colors.length) % colors.length];
      ctx.lineWidth = 2;
      ctx.beginPath();
      arr.forEach((v, i) => {
        const x = 52 + i * ((w - 90) / Math.max(arr.length - 1, 1));
        const y = h - 20 - (v / globalMax) * (h - 60);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  function renderPatients(rows) {
    const host = el("patientList");
    host.innerHTML = "";
    if (!rows.length) {
      host.innerHTML = '<div class="patient-row">No patients in selected district.</div>';
      return;
    }
    rows.slice(0, 15).forEach((p) => {
      const row = document.createElement("div");
      row.className = "patient-row";
      row.innerHTML = `<span>${p.uhid}</span><span>${p.district || "-"}</span>`;
      host.appendChild(row);
    });
  }

  function renderVisits(rows) {
    const host = el("visitList");
    host.innerHTML = "";
    if (!rows.length) {
      host.innerHTML = '<div class="visit-item">No visits available</div>';
      return;
    }
    rows.slice(0, 20).forEach((v) => {
      const block = document.createElement("div");
      block.className = "visit-item";
      const date = (v.visit_date || "").slice(0, 10) || "-";
      block.innerHTML = `<div><strong>${date}</strong></div><div>UHID: ${v.uhid}</div><div>Doctor: ${v.doctor_id || "-"}</div><button type="button">View Report</button>`;
      block.querySelector("button").onclick = () => log({ visit_id: v.visit_id, notes: v.notes || "-", uhid: v.uhid });
      host.appendChild(block);
    });
  }

  function renderMetrics(metrics) {
    el("metricActive").textContent = String(metrics.active_patients || 0);
    el("metricDischarged").textContent = String(metrics.discharged || 0);
    el("metricAppointments").textContent = String(metrics.appointments || 0);
    el("metricTotal").textContent = String(metrics.total_patients || 0);
  }

  async function loadState(name) {
    const data = await parse(await fetch(`/api/government/state/${encodeURIComponent(name)}?` + qs()));
    state.currentStatePayload = data;
    renderDistrictList(data.districts || []);
    if (state.selectedDistrict) {
      await loadDistrict(state.selectedDistrict);
    } else {
      drawTrend([]);
      renderPatients([]);
      renderVisits(data.visits || []);
      renderMetrics(data.metrics || {});
    }
  }

  async function loadDistrict(name) {
    const params = new URLSearchParams();
    params.set("state", state.selectedState);
    params.set("days", el("filterDays").value);
    if (el("filterDisease").value) params.set("disease_category", el("filterDisease").value);
    if (el("filterGender").value) params.set("gender", el("filterGender").value);
    const data = await parse(await fetch(`/api/government/district/${encodeURIComponent(name)}?${params.toString()}`));
    state.currentDistrictPayload = data;
    el("centerTitle").textContent = `${data.district || "-"}, ${data.state || state.selectedState || "-"}`;
    drawTrend(data.trend || []);
    renderPatients(data.patients || []);
    renderVisits(data.visits || []);
    renderMetrics(data.metrics || {});
    log({ selected_state: state.selectedState, selected_district: name, trend_rows: (data.trend || []).length });
  }

  function initSvgBinding() {
    const mapObject = el("indiaSvgObject");
    mapObject.addEventListener("load", () => {
      applyHeatmapToSvg();
      console.log("[GovernmentDashboard] SVG loaded and interactive.");
    });
  }

  function bindButtons() {
    el("loadBtn").addEventListener("click", () => loadStates().catch((e) => log(e.message || String(e))));
  }

  bindButtons();
  initSvgBinding();
  loadStates().catch((e) => log(e.message || String(e)));
})();
