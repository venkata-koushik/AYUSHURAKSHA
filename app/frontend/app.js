const state = {
  patient: { patient_id: "", uhid: "", phone: "", token: "", lastSessionId: "" },
  doctor: { doctor_id: "", token: "", lastStructured: null, notes: "" },
  student: { student_id: "", token: "", lastIncoming: [] },
};

const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data));
  }
  return data;
}

function show(sectionId) {
  ["patientSection", "doctorSection", "studentSection"].forEach((id) => {
    byId(id).classList.toggle("hidden", id !== sectionId);
  });
}

function print(target, data) {
  byId(target).textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

function splitCsv(input) {
  return input
    .split(",")
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
}

function wireRoleSelection() {
  byId("rolePatient").onclick = () => show("patientSection");
  byId("roleDoctor").onclick = () => show("doctorSection");
  byId("roleStudent").onclick = () => show("studentSection");
}

function wirePatient() {
  byId("pRegister").onclick = async () => {
    try {
      const payload = {
        aadhaar: byId("p_aadhaar").value,
        phone: byId("p_phone").value,
        otp: byId("p_otp").value,
        full_name: byId("p_name").value,
        age: Number(byId("p_age").value),
        gender: byId("p_gender").value,
        blood_group: byId("p_blood").value,
        allergies: splitCsv(byId("p_allergies").value),
        chronic_conditions: splitCsv(byId("p_chronic").value),
        address: byId("p_address").value,
        latitude: Number(byId("p_lat").value),
        longitude: Number(byId("p_lng").value),
      };
      const out = await api("/auth/register/patient", { method: "POST", body: JSON.stringify(payload) });
      state.patient.uhid = out.uhid;
      state.patient.phone = payload.phone;
      state.patient.token = out.token.access_token;
      print("patientOutput", out);
      console.log("Patient registration working", out);
    } catch (err) {
      print("patientOutput", err.message);
      console.error(err);
    }
  };

  byId("pLogin").onclick = async () => {
    try {
      const payload = { phone: byId("p_login_phone").value, otp: byId("p_login_otp").value };
      const out = await api("/auth/login/patient-phone", { method: "POST", body: JSON.stringify(payload) });
      state.patient.patient_id = out.user_id;
      state.patient.token = out.access_token;
      byId("pIdentity").textContent = `Logged in Patient ID: ${state.patient.patient_id}`;
      print("patientOutput", out);
      console.log("Patient login working", out);
    } catch (err) {
      print("patientOutput", err.message);
    }
  };

  byId("pDashboard").onclick = async () => {
    if (!state.patient.patient_id) return print("patientOutput", "Login patient first.");
    const out = await api(`/patient/dashboard/${state.patient.patient_id}`);
    print("patientOutput", out);
    if (!state.patient.uhid) state.patient.uhid = out.uhid;
    console.log("Patient dashboard working", out);
  };

  byId("pHealthQr").onclick = async () => {
    if (!state.patient.uhid) return print("patientOutput", "Need UHID first.");
    const out = await api(`/patient/health-qr/${state.patient.uhid}`);
    print("patientOutput", out);
    console.log("My Health QR working", out);
  };

  byId("pAssistant").onclick = async () => {
    const q = byId("pAssistantQuestion").value || "I have mild fever and cough";
    const out = await api(`/patient/ai-assistant?question=${encodeURIComponent(q)}`);
    print("patientOutput", out);
    console.log("AI Health Assistant working", out);
  };

  byId("pReports").onclick = async () => {
    if (!state.patient.uhid) return print("patientOutput", "Need UHID first.");
    const out = await api(`/patient/reports/${state.patient.uhid}`);
    print("patientOutput", out);
    console.log("My Medical Reports working", out);
  };

  byId("pTimeline").onclick = async () => {
    if (!state.patient.uhid) return print("patientOutput", "Need UHID first.");
    const out = await api(`/patient/timeline/${state.patient.uhid}`);
    print("patientOutput", out);
    console.log("Health History Timeline working", out);
  };

  byId("pBook").onclick = async () => {
    if (!state.patient.patient_id) return print("patientOutput", "Login patient first.");
    const payload = {
      patient_id: state.patient.patient_id,
      language: byId("pBookLanguage").value || "English",
      problem: byId("pBookProblem").value || "General consultation",
    };
    try {
      const out = await api("/patient/book-consultation", { method: "POST", body: JSON.stringify(payload) });
      state.patient.lastSessionId = out.session_id;
      print("patientOutput", out);
      console.log("Book Consultation working", out);
    } catch (err) {
      print("patientOutput", err.message);
    }
  };

  byId("pProfile").onclick = () => {
    const out = {
      profile_editable: ["phone", "address", "notifications", "password"],
      aadhaar_editable: false,
      current_patient_id: state.patient.patient_id || "(not logged in)",
    };
    print("patientOutput", out);
    console.log("Profile & Settings button working", out);
  };

  byId("pNotifications").onclick = async () => {
    if (!state.patient.patient_id) return print("patientOutput", "Login patient first.");
    const out = await api(`/patient/notifications/${state.patient.patient_id}`);
    print("patientOutput", out);
    console.log("Patient Notifications working", out);
  };

  byId("pLogout").onclick = () => {
    state.patient = { patient_id: "", uhid: "", phone: "", token: "", lastSessionId: "" };
    byId("pIdentity").textContent = "Logged out";
    print("patientOutput", "Patient logout working");
    console.log("Patient logout working");
  };
}

function wireDoctor() {
  byId("dRegister").onclick = async () => {
    try {
      const payload = {
        government_license_id: byId("d_license").value,
        full_name: byId("d_name").value,
        age: Number(byId("d_age").value),
        username: byId("d_user").value,
        password: byId("d_pass").value,
        confirm_password: byId("d_cpass").value,
      };
      const out = await api("/auth/register/doctor", { method: "POST", body: JSON.stringify(payload) });
      state.doctor.doctor_id = out.doctor_id;
      print("doctorOutput", out);
      console.log("Doctor register working", out);
    } catch (err) {
      print("doctorOutput", err.message);
    }
  };

  byId("dLogin").onclick = async () => {
    try {
      const payload = {
        role: "doctor",
        username: byId("d_login_user").value,
        password: byId("d_login_pass").value,
      };
      const out = await api("/auth/login", { method: "POST", body: JSON.stringify(payload) });
      state.doctor.doctor_id = out.user_id;
      state.doctor.token = out.access_token;
      byId("dIdentity").textContent = `Logged in Doctor ID: ${state.doctor.doctor_id}`;
      print("doctorOutput", out);
      console.log("Doctor login working", out);
    } catch (err) {
      print("doctorOutput", err.message);
    }
  };

  byId("dDashboard").onclick = async () => {
    if (!state.doctor.doctor_id) return print("doctorOutput", "Login doctor first.");
    const out = await api(`/doctor/dashboard/${state.doctor.doctor_id}`);
    print("doctorOutput", out);
    console.log("Doctor dashboard working", out);
  };

  byId("dScan").onclick = async () => {
    if (!state.doctor.doctor_id) return print("doctorOutput", "Login doctor first.");
    const uhid = byId("d_uhid").value;
    const out = await api(`/doctor/scan/${encodeURIComponent(uhid)}?doctor_id=${encodeURIComponent(state.doctor.doctor_id)}`);
    print("doctorOutput", out);
    console.log("Scan patient QR working", out);
  };

  byId("dVoice").onclick = async () => {
    const text = byId("d_voice_text").value || "Patient has fever and cough with hypertension";
    const out = await api("/doctor/process_voice", { method: "POST", body: JSON.stringify({ manual_text: text }) });
    state.doctor.lastStructured = out;
    print("doctorOutput", out);
    console.log("Start Voice Dictation working", out);
  };

  byId("dMic").onclick = async () => {
    const out = await api("/doctor/mic-status");
    print("doctorOutput", out);
    console.log("Mic Status Check working", out);
  };

  byId("dAttach").onclick = () => {
    state.doctor.notes = byId("d_notes").value || "";
    print("doctorOutput", { attached_notes: state.doctor.notes, status: "Manual notes attached in UI state" });
    console.log("Attach Manual Notes working");
  };

  async function saveReport() {
    if (!state.doctor.doctor_id) return print("doctorOutput", "Login doctor first.");
    if (!state.doctor.lastStructured) return print("doctorOutput", "Run Start Voice Dictation first.");
    const payload = {
      uhid: byId("d_uhid").value,
      structured_report: state.doctor.lastStructured,
      doctor_notes: state.doctor.notes || byId("d_notes").value,
    };
    const out = await api(`/doctor/approve-report?doctor_id=${encodeURIComponent(state.doctor.doctor_id)}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    byId("d_visit_id").value = out.visit.visit_id;
    print("doctorOutput", out);
    console.log("Approve & Save / Submit working", out);
  }

  byId("dApprove").onclick = saveReport;
  byId("dSubmit").onclick = saveReport;

  byId("dEdit").onclick = async () => {
    if (!state.doctor.lastStructured) return print("doctorOutput", "Run Start Voice Dictation first.");
    const payload = {
      visit_id: byId("d_visit_id").value,
      structured_report: state.doctor.lastStructured,
      doctor_notes: byId("d_notes").value,
    };
    try {
      const out = await api(`/doctor/edit-report?doctor_id=${encodeURIComponent(state.doctor.doctor_id)}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      print("doctorOutput", out);
      console.log("Edit Report working", out);
    } catch (err) {
      print("doctorOutput", err.message);
    }
  };

  byId("dLogout").onclick = () => {
    state.doctor = { doctor_id: "", token: "", lastStructured: null, notes: "" };
    byId("dIdentity").textContent = "Logged out";
    print("doctorOutput", "Doctor logout working");
    console.log("Doctor logout working");
  };
}

function wireStudent() {
  byId("sRegister").onclick = async () => {
    try {
      const payload = {
        college_id: byId("s_college").value,
        institutional_email: byId("s_email").value,
        otp: byId("s_otp").value,
        password: byId("s_pass").value,
      };
      const out = await api("/auth/register/student", { method: "POST", body: JSON.stringify(payload) });
      state.student.student_id = out.student_id;
      print("studentOutput", out);
      console.log("Student registration working", out);
    } catch (err) {
      print("studentOutput", err.message);
    }
  };

  byId("sLogin").onclick = async () => {
    try {
      const payload = {
        role: "student",
        username: byId("s_login_email").value,
        password: byId("s_login_pass").value,
      };
      const out = await api("/auth/login", { method: "POST", body: JSON.stringify(payload) });
      state.student.student_id = out.user_id;
      state.student.token = out.access_token;
      byId("sIdentity").textContent = `Logged in Student ID: ${state.student.student_id}`;
      print("studentOutput", out);
      console.log("Student login working", out);
    } catch (err) {
      print("studentOutput", err.message);
    }
  };

  byId("sGoOnline").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const payload = { language: byId("s_language").value || "English" };
    const out = await api(`/student/go-online?student_id=${encodeURIComponent(state.student.student_id)}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    print("studentOutput", out);
    console.log("Go Online working", out);
  };

  byId("sDashboard").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const out = await api(`/student/dashboard/${state.student.student_id}`);
    print("studentOutput", out);
    console.log("Student dashboard working", out);
  };

  byId("sIncoming").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const out = await api(`/student/incoming-requests/${state.student.student_id}`);
    state.student.lastIncoming = out;
    if (out.length > 0) byId("s_session_id").value = out[0].session_id;
    print("studentOutput", out);
    console.log("Incoming requests working", out);
  };

  byId("sPast").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const out = await api(`/student/past-sessions/${state.student.student_id}`);
    print("studentOutput", out);
    console.log("Past sessions working", out);
  };

  byId("sRatings").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const out = await api(`/student/ratings/${state.student.student_id}`);
    print("studentOutput", out);
    console.log("Ratings working", out);
  };

  byId("sNotifications").onclick = async () => {
    if (!state.student.student_id) return print("studentOutput", "Login student first.");
    const out = await api(`/student/notifications/${state.student.student_id}`);
    print("studentOutput", out);
    console.log("Student notifications working", out);
  };

  async function decide(accepted) {
    const payload = { session_id: byId("s_session_id").value, accepted };
    const out = await api("/student/session-decision", { method: "POST", body: JSON.stringify(payload) });
    print("studentOutput", out);
    console.log(`Session ${accepted ? "accept" : "reject"} working`, out);
  }

  byId("sAccept").onclick = () => decide(true).catch((e) => print("studentOutput", e.message));
  byId("sReject").onclick = () => decide(false).catch((e) => print("studentOutput", e.message));

  byId("sEnd").onclick = async () => {
    const sessionId = byId("s_session_id").value;
    const out = await api(`/student/end-session/${encodeURIComponent(sessionId)}`, { method: "POST" });
    print("studentOutput", out);
    console.log("End session working", out);
  };

  byId("sSendChat").onclick = () => {
    const message = byId("s_chat").value || "Hello patient, this is a placeholder chat message.";
    print("studentOutput", { websocket_placeholder: true, message });
    console.log("Real-time Chat placeholder working", message);
    console.log("Video Consultation placeholder working (WebRTC to be integrated later)");
  };

  byId("sLogout").onclick = () => {
    state.student = { student_id: "", token: "", lastIncoming: [] };
    byId("sIdentity").textContent = "Logged out";
    print("studentOutput", "Student logout working");
    console.log("Student logout working");
  };
}

function boot() {
  wireRoleSelection();
  wirePatient();
  wireDoctor();
  wireStudent();
  show("patientSection");
  console.log("Frontend loaded and button handlers connected.");
}

boot();
