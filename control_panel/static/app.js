(() => {
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

  let isRunning = false;
  let wsConnected = false;
  let savedPromptValue = "";

  // ---------------- status / run button ----------------

  function setStatus(state, label) {
    statusPill.dataset.state = state;
    statusLabel.textContent = label;
  }

  function applyRunningState(running, { starting = false } = {}) {
    isRunning = running;
    if (starting) {
      runToggle.dataset.state = "starting";
      runLabel.textContent = "Starting…";
      runToggle.disabled = true;
      setStatus("idle", "Starting…");
      return;
    }
    runToggle.disabled = false;
    if (running) {
      runToggle.dataset.state = "running";
      runLabel.textContent = "Stop Vedonica";
      setStatus("running", "Running");
    } else {
      runToggle.dataset.state = "idle";
      runLabel.textContent = "Run Vedonica";
      setStatus(wsConnected ? "idle" : "offline", wsConnected ? "Idle" : "Disconnected");
    }
  }

  runToggle.addEventListener("click", async () => {
    if (isRunning) {
      runToggle.disabled = true;
      runLabel.textContent = "Stopping…";
      try {
        const res = await fetch("/api/stop", { method: "POST" });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          appendSystemLine(body.error || "Couldn't stop the process.");
        }
      } catch (err) {
        appendSystemLine("Network error while stopping: " + err.message);
      }
      runToggle.disabled = false;
    } else {
      applyRunningState(false, { starting: true });
      try {
        const res = await fetch("/api/run", { method: "POST" });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          appendSystemLine(body.error || "Couldn't start local_run.py.");
          applyRunningState(false);
        }
      } catch (err) {
        appendSystemLine("Network error while starting: " + err.message);
        applyRunningState(false);
      }
    }
  });

  // ---------------- console ----------------

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

  clearBtn.addEventListener("click", () => {
    consoleEl.innerHTML = "";
  });

  // ---------------- websocket ----------------

  function connectSocket() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws/logs`);

    ws.addEventListener("open", () => {
      wsConnected = true;
      if (!isRunning) setStatus("idle", "Idle");
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
      } else if (msg.type === "status") {
        applyRunningState(!!msg.running);
      }
    });

    ws.addEventListener("close", () => {
      wsConnected = false;
      setStatus("offline", "Disconnected");
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
          isRunning
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
  fetch("/api/status")
    .then((res) => res.json())
    .then((body) => applyRunningState(!!body.running))
    .catch(() => {});
})();
