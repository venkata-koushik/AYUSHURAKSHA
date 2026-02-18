const store = {
  get(key, fallback = "") {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  },
  set(key, value) {
    localStorage.setItem(key, value);
  },
  clear(keys) {
    keys.forEach((k) => localStorage.removeItem(k));
  },
};

async function api(path, payload, method = "POST") {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });

  const txt = await res.text();
  let data = {};
  try {
    data = txt ? JSON.parse(txt) : {};
  } catch {
    data = { detail: txt || `HTTP ${res.status}` };
  }

  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : (txt || `HTTP ${res.status}`));
  }
  return data;
}

function showOut(id, data) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

function ageFromDob(dob) {
  const d = new Date(dob);
  const t = new Date();
  let a = t.getFullYear() - d.getFullYear();
  const m = t.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && t.getDate() < d.getDate())) a--;
  return a;
}
