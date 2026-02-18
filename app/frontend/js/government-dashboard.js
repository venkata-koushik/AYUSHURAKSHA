(function () {
  const el = (id) => document.getElementById(id);
  const DISEASES = [
    { id: 1, name: "Respiratory", color: "#2563eb" },
    { id: 2, name: "Gastrointestinal", color: "#f59e0b" },
    { id: 3, name: "Musculoskeletal", color: "#16a34a" },
    { id: 4, name: "Neurological", color: "#7c3aed" },
    { id: 5, name: "Dermatological", color: "#db2777" },
    { id: 6, name: "Cardiovascular", color: "#ef4444" },
    { id: 7, name: "Endocrine", color: "#0891b2" },
    { id: 8, name: "Genitourinary", color: "#84cc16" },
    { id: 9, name: "ENT", color: "#f97316" },
    { id: 10, name: "Ophthalmic", color: "#64748b" },
    { id: 11, name: "Mental Health", color: "#0d9488" },
    { id: 12, name: "General / Other", color: "#ca8a04" },
  ];

  const state = {
    map: null,
    geoJsonData: null,
    geoJsonLayer: null,
    stateApi: [],
    selectedState: "",
    selectedDistrict: "",
    selectedPatientUhid: "",
    districtPayload: null,
    chart: null,
    mode: "multi",
    selectedDiseases: new Set(DISEASES.map((d) => d.id)),
    singleDisease: 1,
  };

  function log(v) {
    el("statusLog").textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2);
    console.log("[GovDashboard]", v);
  }

  const govToken = localStorage.getItem("government_token") || window.__GOV_TOKEN__ || "";
  const govUser = localStorage.getItem("government_user") || "";
  const authHeaders = () => (govToken ? { Authorization: "Bearer " + govToken } : {});
  async function apiFetch(url, options) {
    const init = options ? { ...options } : {};
    init.headers = { ...(init.headers || {}), ...authHeaders() };
    return fetch(url, init);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeName(value) {
    return String(value || "").toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]/g, "");
  }

  function renderGovAlerts(items) {
    const host = el("govAlertsList");
    if (!host) return;
    const rows = Array.isArray(items) ? items.slice(0, 12) : [];
    if (!rows.length) {
      host.innerHTML = '<div class="gov-alert-item">No active outbreak alerts.</div>';
      return;
    }
    host.innerHTML = rows.map((n) => {
      return `<div class="gov-alert-item">${escapeHtml(n.message || "Alert")}<small>${escapeHtml((n.created_at || "").replace("T", " ").slice(0, 19))}</small></div>`;
    }).join("");
  }

  async function loadGovAlerts() {
    if (!govUser || !govToken) return;
    try {
      const data = await parse(await apiFetch(`/api/notifications/government/${encodeURIComponent(govUser)}?unread_only=true`));
      const rows = (data.items || []).filter((x) => String(x.type || "") === "outbreak_alert");
      renderGovAlerts(rows);
    } catch (e) {
      log(e.message || String(e));
    }
  }

  function connectGovAlertWs() {
    if (!govUser || !govToken) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws;
    const connect = () => {
      ws = new WebSocket(`${proto}://${location.host}/ws/notifications/government/${encodeURIComponent(govUser)}?token=${encodeURIComponent(govToken)}`);
      ws.onmessage = async (evt) => {
        try {
          const payload = JSON.parse(evt.data || "{}");
          if (payload && payload.type === "outbreak_alert") {
            await loadGovAlerts();
            log(payload.message || "Government outbreak alert received");
          }
        } catch {}
      };
      ws.onclose = () => setTimeout(connect, 1500);
    };
    connect();
  }

  function buildQuery() {
    const q = new URLSearchParams();
    q.set("days", el("filterDays").value || "30");
    if (el("filterDisease").value) q.set("disease_category", el("filterDisease").value);
    if (el("filterGender").value) q.set("gender", el("filterGender").value);
    return q.toString();
  }

  async function parse(res) {
    const txt = await res.text();
    let data = {};
    try { data = txt ? JSON.parse(txt) : {}; } catch { data = { detail: txt || ("HTTP " + res.status) }; }
    if (!res.ok) {
      if (res.status === 401 || res.status === 403) {
        localStorage.removeItem("government_token");
        localStorage.removeItem("government_user");
        location.href = "/government/login";
        throw new Error("Government session expired. Please login again.");
      }
      throw new Error(typeof data.detail === "string" ? data.detail : (txt || ("HTTP " + res.status)));
    }
    return data;
  }

  function dateLabels(days) {
    const out = [];
    const today = new Date();
    for (let i = days - 1; i >= 0; i -= 1) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }

  function initDiseaseDropdown() {
    const host = el("diseaseDropdown");
    host.innerHTML = "";
    DISEASES.forEach((d) => {
      const row = document.createElement("label");
      row.className = "disease-item";
      row.innerHTML = `<input type="checkbox" data-id="${d.id}" checked /> <span>${d.name}</span>`;
      host.appendChild(row);
    });

    el("diseaseDropdownBtn").onclick = () => {
      host.classList.toggle("open");
    };
    document.addEventListener("click", (evt) => {
      if (!host.contains(evt.target) && evt.target !== el("diseaseDropdownBtn")) host.classList.remove("open");
    });
    host.addEventListener("change", (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLInputElement)) return;
      const id = Number(target.getAttribute("data-id"));
      if (!id) return;
      if (state.mode === "single") {
        state.singleDisease = id;
        state.selectedDiseases = new Set([id]);
        target.checked = true;
      } else if (target.checked) {
        state.selectedDiseases.add(id);
      } else {
        state.selectedDiseases.delete(id);
      }
      syncDiseaseUI();
      rerenderDistrictVisuals();
    });
  }

  function syncDiseaseUI() {
    const items = el("diseaseDropdown").querySelectorAll("input[data-id]");
    items.forEach((input) => {
      const id = Number(input.getAttribute("data-id"));
      input.disabled = false;
      if (state.mode === "single") {
        input.checked = id === state.singleDisease;
      } else {
        input.checked = state.selectedDiseases.has(id);
      }
    });
  }

  async function loadStateCounts() {
    const data = await parse(await apiFetch("/api/government/states?" + buildQuery()));
    state.stateApi = data.states || [];
    if (!state.selectedState && state.stateApi.length) {
      state.selectedState = state.stateApi[0].state;
      el("selectedStateName").textContent = state.selectedState;
    }
  }

  function stateCountMap() {
    const dict = {};
    state.stateApi.forEach((s) => {
      dict[normalizeName(s.state)] = {
        count: Number(s.patient_count || s.risk_score || 0),
        rawName: s.state,
      };
    });
    return dict;
  }

  function heatColor(count, min, max) {
    if (max <= min) return "#7fb0e6";
    const t = Math.max(0, Math.min(1, (count - min) / (max - min)));
    const start = { r: 223, g: 236, b: 252 }; // light blue
    const end = { r: 22, g: 91, b: 168 };     // dark blue
    const mix = (a, b) => Math.round(a + (b - a) * t);
    return `rgb(${mix(start.r, end.r)}, ${mix(start.g, end.g)}, ${mix(start.b, end.b)})`;
  }

  function riskLabel(count, min, max) {
    if (max <= min) return "Medium";
    const t = (count - min) / (max - min);
    return t < 0.33 ? "Low" : (t < 0.66 ? "Medium" : "High");
  }

  function renderMapLayer() {
    if (state.geoJsonLayer) state.geoJsonLayer.remove();
    const mapCounts = stateCountMap();
    const vals = Object.values(mapCounts).map((x) => x.count);
    const min = vals.length ? Math.min(...vals) : 0;
    const max = vals.length ? Math.max(...vals) : 1;

    state.geoJsonLayer = L.geoJSON(state.geoJsonData, {
      style: (feature) => {
        const name = feature?.properties?.NAME_1 || feature?.properties?.name || "";
        const data = mapCounts[normalizeName(name)] || { count: 0 };
        return {
          color: normalizeName(name) === normalizeName(state.selectedState) ? "#0f3f7c" : "#7ca6d8",
          weight: normalizeName(name) === normalizeName(state.selectedState) ? 3 : 1.4,
          fillColor: heatColor(data.count, min, max),
          fillOpacity: 0.85,
        };
      },
      onEachFeature: (feature, layer) => {
        const featureName = feature?.properties?.NAME_1 || feature?.properties?.name || "Unknown";
        const hit = mapCounts[normalizeName(featureName)] || { count: 0, rawName: featureName };
        const risk = riskLabel(hit.count, min, max);
        layer.bindTooltip(`State Name: ${featureName}<br>Total Patients: ${hit.count}<br>Risk Level: ${risk}`, { sticky: true });
        layer.on("click", async (evt) => {
          state.selectedState = hit.rawName || featureName;
          state.selectedDistrict = "";
          el("selectedStateName").textContent = state.selectedState;
          state.map.fitBounds(evt.target.getBounds(), { padding: [16, 16] });
          renderMapLayer();
          await loadStateDetails(state.selectedState);
        });
      },
    }).addTo(state.map);
  }

  function renderDistrictList(districts) {
    const host = el("districtList");
    host.innerHTML = "";
    if (!districts.length) {
      host.textContent = "No district records found for this state in patient data.";
      return;
    }
    if (!state.selectedDistrict) state.selectedDistrict = districts[0].district;
    districts.forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "district-item" + (d.district === state.selectedDistrict ? " active" : "");
      btn.textContent = d.district;
      btn.onclick = async () => {
        state.selectedDistrict = d.district;
        renderDistrictList(districts);
        await loadDistrictDetails(state.selectedDistrict);
      };
      host.appendChild(btn);
    });
  }

  function renderSummary(metrics) {
    el("summaryActive").textContent = String(metrics.active_patients || 0);
    el("summaryDischarged").textContent = String(metrics.discharged || 0);
    el("summaryAppointments").textContent = String(metrics.appointments || 0);
    el("summaryTotal").textContent = String(metrics.total_patients || 0);
  }

  function renderVisits(visits) {
    const host = el("visitList");
    host.innerHTML = "";
    if (!visits.length) {
      host.innerHTML = '<div class="visit-item">No recent visits.</div>';
      return;
    }
    visits.slice(0, 20).forEach((v) => {
      const dt = (v.visit_date || "").slice(0, 10) || "-";
      const item = document.createElement("div");
      item.className = "visit-item";
      item.innerHTML = `<div><strong>${dt}</strong></div><div>Diagnosis category: ${v.diagnosis_category || "General"}</div><button type="button">View</button>`;
      item.querySelector("button").onclick = () => {
        el("reportDetails").textContent = `Visit ${v.visit_id || "-"} | UHID ${v.uhid || "-"} | Notes: ${v.notes || "-"}`;
      };
      host.appendChild(item);
    });
  }

  function renderPatients(patients, limit) {
    const host = el("patientList");
    host.innerHTML = "";
    host.style.height = "260px";
    host.style.minHeight = "260px";
    host.style.maxHeight = "260px";
    host.style.overflowY = "auto";
    host.style.overflowX = "hidden";
    const rows = (patients || []).slice(0, limit);
    if (!rows.length) {
      host.innerHTML = '<div class="patient-row">No patient rows.</div>';
      return;
    }
    rows.forEach((p) => {
      const row = document.createElement("div");
      row.className = "patient-row" + (state.selectedPatientUhid === p.uhid ? " active" : "");
      row.innerHTML = `<button type="button" class="uhid-btn">${escapeHtml(p.uhid || "-")}</button><span>Age: ${p.age ?? "-"}</span>`;
      row.querySelector(".uhid-btn")?.addEventListener("click", async () => {
        if (!p.uhid) return;
        state.selectedPatientUhid = p.uhid;
        renderPatients(patients, limit);
        await loadPatientHistory(p.uhid);
      });
      host.appendChild(row);
    });
    if ((patients || []).length > rows.length) {
      const tail = document.createElement("div");
      tail.className = "patient-row";
      tail.innerHTML = `<span>Showing ${rows.length} of ${(patients || []).length}</span><span>Scroll list</span>`;
      host.appendChild(tail);
    }
  }

  function renderPatientHistory(historyPayload) {
    const host = el("reportDetails");
    const history = Array.isArray(historyPayload?.history) ? historyPayload.history : [];
    if (!history.length) {
      host.textContent = `No reports found for ${historyPayload?.uhid || "-"}.`;
      return;
    }
    const html = history.map((entry) => {
      const v = entry.visit || {};
      const diagnoses = Array.isArray(entry.diagnosis) ? entry.diagnosis : [];
      const recs = Array.isArray(entry.recommendations) ? entry.recommendations : [];
      const dText = diagnoses.length
        ? diagnoses.map((d) => `Cat ${d.disease_category}${d.disease_name ? ` (${escapeHtml(d.disease_name)})` : ""}`).join(", ")
        : "None";
      const rText = recs.length
        ? recs.map((r) => escapeHtml(r.advice_text || "-")).join("; ")
        : "None";
      return `
        <div class="history-item">
          <div><strong>Visit:</strong> ${escapeHtml(v.visit_id || "-")} | <strong>Date:</strong> ${escapeHtml((v.visit_date || "").slice(0, 10) || "-")}</div>
          <div><strong>Doctor:</strong> ${escapeHtml(v.doctor_id || "-")} | <strong>Type:</strong> ${escapeHtml(v.consultation_type || "-")}</div>
          <div><strong>Notes:</strong> ${escapeHtml(v.notes || "-")}</div>
          <div><strong>Diagnosis:</strong> ${dText}</div>
          <div><strong>Recommendation:</strong> ${rText}</div>
        </div>
      `;
    }).join("");
    host.innerHTML = `<div class="history-title">Reports for UHID ${escapeHtml(historyPayload.uhid || "-")}</div>${html}`;
  }

  async function loadPatientHistory(uhid) {
    el("reportDetails").textContent = `Loading reports for ${uhid}...`;
    try {
      const data = await parse(await fetch(`/api/patient_history/${encodeURIComponent(uhid)}`));
      renderPatientHistory(data);
      log({ selected_patient_uhid: uhid, reports: (data.history || []).length });
    } catch (e) {
      el("reportDetails").textContent = `Unable to load reports for ${uhid}: ${e.message || e}`;
    }
  }

  function buildGraphSeries(payload) {
    const days = Number(el("filterDays").value || "30");
    const labels = dateLabels(days);
    const diseaseTimeSeries = payload?.disease_time_series || [];

    const selected = state.mode === "single" ? [state.singleDisease] : Array.from(state.selectedDiseases).sort((a, b) => a - b);
    if (!selected.length) selected.push(1);

    const seriesByCategory = {};
    diseaseTimeSeries.forEach((row) => {
      const cat = Number(row.disease_category || 0);
      const day = String(row.date || "").slice(0, 10);
      const count = Number(row.patient_count || 0);
      if (!cat || !day) return;
      if (!seriesByCategory[cat]) seriesByCategory[cat] = {};
      seriesByCategory[cat][day] = (seriesByCategory[cat][day] || 0) + count;
    });

    const datasets = selected.map((id, idx) => {
      const info = DISEASES.find((d) => d.id === id) || { id, name: `Category ${id}`, color: "#1e5da8" };
      const data = labels.map((day) => Number(seriesByCategory[id]?.[day] || 0));
      return {
        id,
        label: info.name,
        color: state.mode === "single" ? "#1e5da8" : info.color,
        data,
      };
    });

    // Strict real-data mode: keep zeros when no time-series rows exist.
    // No synthetic or inferred spike values should be injected.

    return { labels, datasets };
  }

  function renderLegend(datasets) {
    const host = el("graphLegend");
    host.innerHTML = "";
    datasets.forEach((d) => {
      const item = document.createElement("div");
      item.className = "legend-item";
      item.innerHTML = `<span class="legend-dot" style="background:${d.color}"></span><span>${d.label}</span>`;
      host.appendChild(item);
    });
  }

  function renderChart(payload) {
    const ctx = el("trendChart").getContext("2d");
    const series = buildGraphSeries(payload);
    const chartData = {
      labels: series.labels,
      datasets: series.datasets.map((d) => ({
        label: d.label,
        data: d.data,
        borderColor: d.color,
        backgroundColor: d.color,
        tension: 0.35,
      })),
    };
    const allValues = series.datasets
      .flatMap((d) => d.data || [])
      .map((n) => Number(n))
      .filter((n) => Number.isFinite(n));
    const maxValue = allValues.length ? Math.max(...allValues) : 0;
    const yMax = maxValue <= 5 ? 5 : Math.ceil(maxValue * 1.1);
    const yStep = yMax <= 10 ? 1 : Math.max(1, Math.ceil(yMax / 8));

    if (state.chart) state.chart.destroy();
    state.chart = new Chart(ctx, {
      type: "line",
      data: chartData,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { title: { display: true, text: "Date" } },
          y: {
            beginAtZero: true,
            min: 0,
            suggestedMax: yMax,
            title: { display: true, text: "Patient count" },
            ticks: {
              stepSize: yStep,
              precision: 0,
              callback: (value) => String(Math.round(Number(value) || 0)),
            },
          },
        },
      },
    });
    renderLegend(series.datasets);

    const totals = series.labels.map((_, idx) => series.datasets.reduce((sum, d) => sum + (d.data[idx] || 0), 0));
    const latest = totals[totals.length - 1] || 0;
    const prev = totals[totals.length - 2] || 0;
    el("districtActivePatients").textContent = String(latest);
    el("districtNewPatients").textContent = String(latest - prev);
  }

  async function loadStateDetails(name) {
    const data = await parse(await apiFetch(`/api/government/state/${encodeURIComponent(name)}?${buildQuery()}`));
    renderDistrictList(data.districts || []);
    renderSummary(data.metrics || {});
    renderVisits(data.visits || []);
    if (state.selectedDistrict) {
      await loadDistrictDetails(state.selectedDistrict);
    } else {
      renderPatients(data.patients || [], 10);
      el("districtTitle").textContent = `-, ${state.selectedState}`;
      renderChart({ disease_stats: [], patient_trend: [], trend: [] });
    }
  }

  async function loadDistrictDetails(districtName) {
    const params = new URLSearchParams();
    params.set("state", state.selectedState || "");
    params.set("days", el("filterDays").value || "30");
    if (el("filterGender").value) params.set("gender", el("filterGender").value);
    if (el("filterDisease").value) params.set("disease_category", el("filterDisease").value);
    const data = await parse(await apiFetch(`/api/government/district/${encodeURIComponent(districtName)}?${params.toString()}`));
    state.districtPayload = data;
    el("districtTitle").textContent = `${data.district || districtName}, ${data.state || state.selectedState || "-"}`;
    renderSummary(data.metrics || {});
    renderVisits(data.visits || []);
    renderPatients(data.patients || [], 10);
    renderChart(data);
    log({ selected_state: state.selectedState, selected_district: districtName, mode: state.mode });
  }

  function rerenderDistrictVisuals() {
    if (!state.districtPayload) return;
    renderChart(state.districtPayload);
  }

  function bindActions() {
    el("applyFiltersBtn").onclick = async () => {
      await loadStateCounts();
      renderMapLayer();
      if (state.selectedState) await loadStateDetails(state.selectedState);
    };
    el("viewAllPatientsBtn").onclick = async () => {
      if (!state.selectedDistrict) return;
      const params = new URLSearchParams();
      params.set("state", state.selectedState || "");
      params.set("days", el("filterDays").value || "30");
      const data = await parse(await apiFetch(`/api/government/district/${encodeURIComponent(state.selectedDistrict)}?${params.toString()}`));
      // Keep panel fixed-height; "View All" only increases scrollable rows.
      renderPatients(data.patients || [], 80);
    };
    el("multiColorModeBtn").onclick = () => {
      state.mode = "multi";
      el("multiColorModeBtn").classList.add("active");
      el("singleColorModeBtn").classList.remove("active");
      if (!state.selectedDiseases.size) state.selectedDiseases = new Set(DISEASES.map((d) => d.id));
      syncDiseaseUI();
      rerenderDistrictVisuals();
    };
    el("singleColorModeBtn").onclick = () => {
      state.mode = "single";
      el("singleColorModeBtn").classList.add("active");
      el("multiColorModeBtn").classList.remove("active");
      if (!state.singleDisease) state.singleDisease = Array.from(state.selectedDiseases)[0] || 1;
      state.selectedDiseases = new Set([state.singleDisease]);
      syncDiseaseUI();
      rerenderDistrictVisuals();
    };
  }

  async function initMapAndData() {
    const indiaFrame = L.latLngBounds([5.2, 67.2], [37.8, 97.6]);
    state.map = L.map("map", {
      preferCanvas: true,
      minZoom: 4.2,
      maxZoom: 8.5,
      zoomSnap: 0.25,
      maxBoundsViscosity: 1.0,
      worldCopyJump: false,
    }).setView([22.5937, 78.9629], 5.2);
    state.map.setMaxBounds(indiaFrame);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      noWrap: true,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(state.map);

    const geo = await fetch("/static/geo/india_states.geojson");
    if (!geo.ok) throw new Error("Failed to load India states GeoJSON");
    state.geoJsonData = await geo.json();

    await loadStateCounts();
    renderMapLayer();
    // Lock map interaction to India bounds only.
    if (state.geoJsonLayer) {
      const indiaBounds = state.geoJsonLayer.getBounds().pad(0.06);
      state.map.setMaxBounds(indiaBounds);
      state.map.fitBounds(indiaBounds, { padding: [8, 8] });
    }
    if (state.selectedState) {
      el("selectedStateName").textContent = state.selectedState;
      await loadStateDetails(state.selectedState);
    }
  }

  initDiseaseDropdown();
  syncDiseaseUI();
  bindActions();
  loadGovAlerts();
  connectGovAlertWs();
  initMapAndData().catch((e) => log(e.message || String(e)));
})();
