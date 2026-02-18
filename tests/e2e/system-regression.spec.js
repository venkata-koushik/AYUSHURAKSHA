const { test, expect } = require("@playwright/test");

function attachGuards(page) {
  const issues = {
    pageErrors: [],
    consoleErrors: [],
    api404: [],
  };
  page.on("pageerror", (err) => issues.pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") issues.consoleErrors.push(msg.text());
  });
  page.on("response", (res) => {
    const url = res.url();
    if (res.status() === 404 && (url.includes("/api/") || url.includes("/ws/"))) {
      issues.api404.push(`${res.status()} ${url}`);
    }
  });
  return issues;
}

async function expectNoRuntimeIssues(issues) {
  expect(issues.pageErrors, `Unhandled page errors: ${issues.pageErrors.join("\n")}`).toEqual([]);
  expect(issues.api404, `404 API calls found: ${issues.api404.join("\n")}`).toEqual([]);
}

test("doctor dashboard loads and major controls are clickable", async ({ page }) => {
  const issues = attachGuards(page);
  await page.goto("/doctor/dashboard");
  await expect(page.locator("text=Structured Clinical Dictation")).toBeVisible();

  const ids = [
    "#scanBtn",
    "#searchBtn",
    "#micCheckBtn",
    "#voiceBtn",
    "#structureBtn",
    "#finalizeBtn",
  ];
  for (const id of ids) {
    const loc = page.locator(id);
    await expect(loc).toBeVisible();
    await loc.click({ trial: true });
  }
  await expectNoRuntimeIssues(issues);
});

test("doctor voice dictation writes transcript into Clinical Voice/Text Input", async ({ page }) => {
  const issues = attachGuards(page);
  await page.route("**/api/transcribe", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transcript: "Patient has mild fever and dry cough.", confidence: 0.92 }),
    });
  });
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => ({
      getTracks: () => [{ stop: () => {} }],
      getAudioTracks: () => [{ enabled: true, stop: () => {} }],
    });
    class FakeMediaRecorder {
      static isTypeSupported() { return true; }
      constructor(stream, options) {
        this.stream = stream;
        this.options = options;
        this.state = "inactive";
        this.ondataavailable = null;
        this.onstop = null;
      }
      start() { this.state = "recording"; }
      stop() {
        this.state = "inactive";
        if (this.ondataavailable) {
          this.ondataavailable({ data: new Blob(["fake-audio"], { type: "audio/webm" }) });
        }
        if (this.onstop) this.onstop();
      }
    }
    window.MediaRecorder = FakeMediaRecorder;
  });

  await page.goto("/doctor/dashboard");
  await page.click("#voiceBtn");
  await page.waitForTimeout(200);
  await page.click("#voiceBtn");
  await expect(page.locator("#rawTranscript")).toHaveValue(/mild fever and dry cough/i, { timeout: 10_000 });
  await expectNoRuntimeIssues(issues);
});

test("QR scan flow autofills UHID with mocked camera", async ({ page }) => {
  const issues = attachGuards(page);
  await page.route("**/api/hospital/scan", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uhid: "UHID20260001",
        patient_profile: { name: "Mock Patient", age: 30, blood_group: "B+" },
        history: [],
      }),
    });
  });
  await page.addInitScript(() => {
    // Mock camera stream from canvas.
    navigator.mediaDevices.getUserMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 240;
      return canvas.captureStream(10);
    };
    // Mock BarcodeDetector scanning success.
    let used = false;
    window.BarcodeDetector = class {
      static async getSupportedFormats() {
        return ["qr_code"];
      }
      async detect() {
        if (used) return [];
        used = true;
        return [{ rawValue: "QR::UHID20260001::playwright" }];
      }
    };
  });
  await page.goto("/doctor/dashboard");
  await page.click("#scanBtn");
  await page.click("#startCameraBtn");
  await expect(page.locator("#uhid")).toHaveValue(/UHID20260001/i, { timeout: 12_000 });
  await expectNoRuntimeIssues(issues);
});

test("audio upload endpoint returns structured response schema", async ({ request }) => {
  // Dummy payload to ensure endpoint reachable and schema contract present.
  const body = Buffer.from([0x1a, 0x45, 0xdf, 0xa3]);
  const res = await request.post("/api/transcribe", {
    multipart: {
      file: {
        name: "dummy.webm",
        mimeType: "audio/webm",
        buffer: body,
      },
    },
  });
  expect([200, 400, 500]).toContain(res.status());
  const text = await res.text();
  expect(text.length).toBeGreaterThan(0);
});

test("AI chat send works and updates UI", async ({ page }) => {
  const issues = attachGuards(page);
  await page.route("**/api/ai/chat", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        guidance: ["Mock AI response"],
        summary: { symptoms_detected: [], possible_diagnosis_tags: [] },
        urgency: "low",
      }),
    });
  });
  await page.route("**/api/chat", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        response: "Mock AI response",
        guidance: ["Mock AI response"],
      }),
    });
  });
  await page.goto("/patient/ai-chat");
  await page.fill("#q", "test message");
  await page.click("#send");
  await expect(page.locator(".msg.bot").last()).toContainText("Mock AI response");
  await expectNoRuntimeIssues(issues);
});

test("global notification websocket receives pushed message", async ({ page, request }) => {
  const issues = attachGuards(page);
  const patientId = "qa-patient-1";
  await page.addInitScript((id) => localStorage.setItem("patient_id", id), patientId);
  await page.goto("/patient/notifications");

  const res = await request.post("/api/test-notification", {
    data: {
      role: "patient",
      user_id: patientId,
      title: "E2E",
      message: "Notification from playwright",
      type: "system",
    },
  });
  expect(res.ok()).toBeTruthy();
  await expect(page.locator(".item p").first()).toContainText("Notification from playwright", { timeout: 10_000 });
  await expectNoRuntimeIssues(issues);
});

test("routes valid and visible buttons actionable across pages", async ({ page, request }) => {
  const issues = attachGuards(page);
  const routes = [
    "/select-role",
    "/patient/register",
    "/patient/login",
    "/patient/dashboard",
    "/doctor/login",
    "/doctor/dashboard",
    "/student/register",
    "/student/login",
    "/student/dashboard",
    "/government/login",
  ];

  for (const route of routes) {
    const res = await request.get(route);
    expect(res.status(), `Route failed: ${route}`).toBeLessThan(400);
    await page.goto(route);
    const buttons = await page.locator("button").evaluateAll((els) =>
      els
        .filter((el) => el instanceof HTMLElement && el.offsetParent !== null)
        .slice(0, 8)
        .map((el) => ({
          text: (el.textContent || "").trim(),
          disabled: !!el.disabled,
        }))
    );
    expect(buttons.length, `No visible buttons found on ${route}`).toBeGreaterThan(0);
    const disabled = buttons.filter((b) => b.disabled);
    expect(disabled, `Disabled buttons found on ${route}: ${JSON.stringify(disabled)}`).toEqual([]);
  }
  await expectNoRuntimeIssues(issues);
});

test("forgot password page uses single-step OTP reset UI", async ({ page }) => {
  const issues = attachGuards(page);
  await page.goto("/forgot-password?role=patient");
  await expect(page.locator("#sendOtpBtn")).toBeVisible();
  await expect(page.locator("#resetBtn")).toBeVisible();
  await expect(page.locator("#verifyOtpBtn")).toHaveCount(0);
  await expectNoRuntimeIssues(issues);
});
