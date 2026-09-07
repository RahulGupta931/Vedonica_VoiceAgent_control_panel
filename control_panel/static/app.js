import { PipecatClient, RTVIEvent } from "https://cdn.jsdelivr.net/npm/@pipecat-ai/client-js@1.13.0/+esm";
import { SmallWebRTCTransport } from "https://cdn.jsdelivr.net/npm/@pipecat-ai/small-webrtc-transport@1.10.5/+esm";

const statusPill = document.getElementById("status-pill");
const statusLabel = document.getElementById("status-label");
const runToggle = document.getElementById("run-toggle");
const runLabel = runToggle.querySelector(".btn-label");
const consoleEl = document.getElementById("console");
const autoscrollBox = document.getElementById("autoscroll");
const clearBtn = document.getElementById("clear-console");

const promptEditor = document.getElementById("prompt-editor");
const charCount = document.getElementById("char-count");
const saveBtn = document.getElementById("save-prompt");
const reloadBtn = document.getElementById("reload-prompt");
const revertBtn = document.getElementById("revert-prompt");
const promptMessage = document.getElementById("prompt-message");

let wsConnected = false;
let savedPromptValue = "";
let voiceClient = null;
let botAudioEl = null;
let micEnabledForSession = false;
let micEnableTimer = null;

// User intent is the single source of truth for whether a session should be live.
let userWantsSession = false;
let sessionGeneration = 0;
let actionInProgress = false;

function isCurrentSession(generation) {
  return userWantsSession && generation === sessionGeneration;
}

/** Prompt for mic on the button click (required for browser permission UI). */
async function ensureMicPermission() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
    },
    video: false,
  });
  stream.getTracks().forEach((track) => track.stop());
}

function clearMicEnableTimer() {
  if (micEnableTimer) {
    clearTimeout(micEnableTimer);
    micEnableTimer = null;
  }
}

function setStatus(state, label) {
  statusPill.dataset.state = state;
  statusLabel.textContent = label;
}

function updateRunButton({ mode = "idle" }) {
  runToggle.disabled = mode === "stopping";

  if (mode === "starting" || mode === "running") {
    runToggle.dataset.state = mode;
    runLabel.textContent = "Stop Vedonica";
    setStatus("running", mode === "starting" ? "Starting…" : "Running");
    return;
  }

  if (mode === "stopping") {
    runToggle.dataset.state = "stopping";
    runLabel.textContent = "Stopping…";
    setStatus("running", "Stopping…");
    return;
  }

  runToggle.dataset.state = "idle";
  runLabel.textContent = "Run Vedonica";
  setStatus(wsConnected ? "idle" : "offline", wsConnected ? "Idle" : "Disconnected");
}

function appendLine(text, cls) {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = text;
  consoleEl.appendChild(line);
  if (autoscrollBox.checked) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

function appendSystemLine(text) {
  appendLine(text, "line-system");
}

function classifyLine(text) {
  const lower = text.toLowerCase();
  if (lower.includes("error") || lower.includes("traceback") || lower.includes("exception")) {
    return "line-error";
  }
  if (lower.includes("warn")) return "line-warn";
  return "";
}

function cleanupBotAudio() {
  if (botAudioEl) {
    botAudioEl.pause();
    botAudioEl.srcObject = null;
    botAudioEl.remove();
    botAudioEl = null;
  }
}

function enableUserMic(generation) {
  if (!isCurrentSession(generation) || micEnabledForSession || !voiceClient) return;
  clearMicEnableTimer();
  micEnabledForSession = true;
  voiceClient.enableMic(true);
  appendSystemLine("Your turn — speak now.");
}

async function teardownClient(client) {
  if (!client) return;
  try {
    client.enableMic(false);
    if (typeof client.disconnectBot === "function") {
      client.disconnectBot();
    }
    await Promise.race([
      client.disconnect(),
      new Promise((resolve) => setTimeout(resolve, 4000)),
    ]);
  } catch (_err) {
    // Ignore teardown errors.
  }
}

async function startVoiceSession() {
  if (userWantsSession) return;

  const generation = ++sessionGeneration;
  userWantsSession = true;
  micEnabledForSession = false;
  clearMicEnableTimer();
  updateRunButton({ mode: "starting" });
  appendSystemLine("Requesting microphone access…");

  let client = null;

  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser does not support microphone access. Use HTTPS and a modern browser.");
    }

    await ensureMicPermission();
    if (!isCurrentSession(generation)) return;

    appendSystemLine("Microphone access granted. Connecting to Vedonica…");

    const transport = new SmallWebRTCTransport({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });

    client = new PipecatClient({
      transport,
      enableMic: true,
      enableCam: false,
      disconnectOnBotDisconnect: true,
      callbacks: {
        onConnected: () => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          client.enableMic(false);
          updateRunButton({ mode: "running" });
          appendSystemLine("Connected — Vedonica will greet you first. Please listen.");
        },
        onDisconnected: () => {
          if (voiceClient !== client) return;
          cleanupBotAudio();
          voiceClient = null;
          if (userWantsSession && generation === sessionGeneration) {
            // Unexpected disconnect while user still wants a session.
            userWantsSession = false;
            updateRunButton({ mode: "idle" });
            appendSystemLine("Disconnected.");
          }
        },
        onBotReady: () => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          appendSystemLine("Vedonica is speaking her greeting…");
          clearMicEnableTimer();
          micEnableTimer = setTimeout(() => enableUserMic(generation), 12000);
        },
        onBotStoppedSpeaking: () => {
          enableUserMic(generation);
        },
        onUserTranscript: (data) => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          if (data.final) appendLine(`You: ${data.text}`, "line-user");
        },
        onBotTranscript: (data) => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          appendLine(`Vedonica: ${data.text}`, "line-bot");
        },
        onError: (error) => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          appendSystemLine(`Error: ${error.message || error}`);
          userWantsSession = false;
          updateRunButton({ mode: "idle" });
        },
        onTransportStateChanged: (state) => {
          if (!isCurrentSession(generation) || voiceClient !== client) return;
          if (state === "connecting" || state === "connected" || state === "ready" || state === "error") {
            appendSystemLine(`Connection: ${state}`);
          }
          if (state === "error") {
            appendSystemLine("Connection failed. Check your network and try again.");
            userWantsSession = false;
            updateRunButton({ mode: "idle" });
          }
        },
      },
    });

    client.on(RTVIEvent.TrackStarted, (track, participant) => {
      if (!isCurrentSession(generation) || voiceClient !== client) return;
      if (!participant?.local && track.kind === "audio") {
        cleanupBotAudio();
        botAudioEl = document.createElement("audio");
        botAudioEl.autoplay = true;
        botAudioEl.playsInline = true;
        botAudioEl.srcObject = new MediaStream([track]);
        document.body.appendChild(botAudioEl);
        appendSystemLine("Speaker output active.");
      }
    });

    voiceClient = client;

    await client.startBotAndConnect({
      endpoint: `${location.origin}/start`,
      requestData: {
        transport: "webrtc",
        enableDefaultIceServers: true,
      },
    });

    if (!isCurrentSession(generation) || voiceClient !== client) {
      await teardownClient(client);
      if (voiceClient === client) voiceClient = null;
      return;
    }

    updateRunButton({ mode: "running" });
  } catch (err) {
    if (!userWantsSession || generation !== sessionGeneration) {
      await teardownClient(client);
      if (voiceClient === client) voiceClient = null;
      return;
    }

    let message = err.message || String(err);
    if (err.name === "NotAllowedError" || message.toLowerCase().includes("permission")) {
      message = "Microphone permission denied. Allow mic access in your browser settings and try again.";
    } else if (err.name === "NotFoundError") {
      message = "No microphone found. Plug in a mic and try again.";
    }
    appendSystemLine(message);
    cleanupBotAudio();
    await teardownClient(client);
    voiceClient = null;
    userWantsSession = false;
    updateRunButton({ mode: "idle" });
  }
}

async function stopVoiceSession() {
  if (!userWantsSession) return;

  // Kill user intent first so no late callback can restart the session.
  userWantsSession = false;
  sessionGeneration += 1;
  clearMicEnableTimer();
  micEnabledForSession = false;
  updateRunButton({ mode: "stopping" });

  const client = voiceClient;
  voiceClient = null;

  try {
    await fetch("/api/stop", { method: "POST" });
  } catch (err) {
    appendSystemLine("Error stopping server: " + err.message);
  }

  await teardownClient(client);
  cleanupBotAudio();
  updateRunButton({ mode: "idle" });
  appendSystemLine("Session stopped.");
}

runToggle.addEventListener("click", async () => {
  if (actionInProgress) return;
  actionInProgress = true;
  try {
    if (userWantsSession) {
      await stopVoiceSession();
    } else {
      await startVoiceSession();
    }
  } finally {
    actionInProgress = false;
  }
});

// ---------------- console ----------------

clearBtn.addEventListener("click", () => {
  consoleEl.innerHTML = "";
});

// ---------------- websocket (logs only — never drives run/stop UI) ----------------

function connectSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/logs`);

  ws.addEventListener("open", () => {
    wsConnected = true;
    if (!userWantsSession) setStatus("idle", "Idle");
  });

  ws.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "log") {
      appendLine(msg.line, classifyLine(msg.line));
    }
  });

  ws.addEventListener("close", () => {
    wsConnected = false;
    if (!userWantsSession) setStatus("offline", "Disconnected");
    setTimeout(connectSocket, 2000);
  });

  ws.addEventListener("error", () => ws.close());
}

connectSocket();

// ---------------- prompt editor ----------------

function updateCharCount() {
  charCount.textContent = `${promptEditor.value.length.toLocaleString()} characters`;
}

function setPromptMessage(text, kind) {
  promptMessage.textContent = text;
  if (kind) {
    promptMessage.dataset.kind = kind;
  } else {
    delete promptMessage.dataset.kind;
  }
}

function hasUnsavedChanges() {
  return promptEditor.value !== savedPromptValue;
}

async function loadPrompt({ quiet = false } = {}) {
  if (!quiet) setPromptMessage("Loading…");
  try {
    const res = await fetch("/api/prompt");
    const body = await res.json();
    if (!res.ok) {
      setPromptMessage(body.error || "Couldn't load the prompt.", "error");
      return;
    }
    promptEditor.value = body.prompt;
    savedPromptValue = body.prompt;
    revertBtn.disabled = !body.has_backup;
    updateCharCount();
    setPromptMessage(quiet ? "" : "Loaded from app/prompts.py.");
  } catch (err) {
    setPromptMessage("Network error: " + err.message, "error");
  }
}

promptEditor.addEventListener("input", () => {
  updateCharCount();
  if (hasUnsavedChanges()) {
    setPromptMessage("Unsaved changes.");
  } else {
    setPromptMessage("");
  }
});

saveBtn.addEventListener("click", async () => {
  saveBtn.disabled = true;
  setPromptMessage("Saving…");
  try {
    const res = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: promptEditor.value }),
    });
    const body = await res.json();
    if (!res.ok) {
      setPromptMessage(body.error || "Couldn't save the prompt.", "error");
    } else {
      savedPromptValue = promptEditor.value;
      revertBtn.disabled = false;
      setPromptMessage(
        userWantsSession
          ? "Saved. Stop and run again to pick up the change."
          : "Saved to app/prompts.py.",
        "ok"
      );
    }
  } catch (err) {
    setPromptMessage("Network error: " + err.message, "error");
  } finally {
    saveBtn.disabled = false;
  }
});

reloadBtn.addEventListener("click", () => {
  if (hasUnsavedChanges() && !confirm("Discard unsaved changes and reload from file?")) {
    return;
  }
  loadPrompt();
});

revertBtn.addEventListener("click", async () => {
  if (!confirm("Restore the prompt from before your first save? This overwrites the current file.")) {
    return;
  }
  revertBtn.disabled = true;
  setPromptMessage("Reverting…");
  try {
    const res = await fetch("/api/prompt/revert", { method: "POST" });
    const body = await res.json();
    if (!res.ok) {
      setPromptMessage(body.error || "Couldn't revert.", "error");
    } else {
      promptEditor.value = body.prompt;
      savedPromptValue = body.prompt;
      updateCharCount();
      setPromptMessage("Reverted to the backed-up version.", "ok");
    }
  } catch (err) {
    setPromptMessage("Network error: " + err.message, "error");
  } finally {
    revertBtn.disabled = false;
  }
});

window.addEventListener("beforeunload", (event) => {
  if (hasUnsavedChanges()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

// ---------------- init ----------------

revertBtn.disabled = true;
loadPrompt();
updateRunButton({ mode: "idle" });
