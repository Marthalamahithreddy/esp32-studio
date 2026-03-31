/* =============================================================================
   ESP32 STUDIO — main.js
   All browser-side logic. Talks to Flask via fetch().

   BLOCK INDEX:
   1  → Constants & default sketch
   2  → ESP32 pinout data
   3  → App state object
   4  → DOM helpers
   5  → Init (runs on DOMContentLoaded)
   6  → Editor: line numbers, cursor, tab key, scroll sync
   7  → Left pane: section switcher + card filter
   8  → Pinout grid renderer
   9  → Output panel: log helpers, tab switch, clear, autoscroll
   10 → API: verifyCode() — POST /api/compile
   11 → API: uploadCode() — POST /api/upload
   12 → Sketch loader: dropdown, card click, new sketch
   13 → Board selector
   14 → Resizer (drag to resize panes)
   15 → Keyboard shortcuts
   16 → Loading overlay helpers
   17 → Utility functions
============================================================================= */


// =============================================================================
// BLOCK 1: CONSTANTS
// =============================================================================

// Default sketch shown when the page loads
const DEFAULT_SKETCH = `// ============================================
// ESP32 Studio — New Sketch
// Board: ESP32 Dev Module @ 240MHz
// ============================================

void setup() {
  // Runs ONCE on power-on or reset
  Serial.begin(115200);          // Init serial at 115200 baud
  pinMode(LED_BUILTIN, OUTPUT);  // Set GPIO2 as output
  Serial.println("Hello from ESP32 Studio!");
}

void loop() {
  // Runs FOREVER after setup()
  digitalWrite(LED_BUILTIN, HIGH);  // LED on
  delay(500);
  digitalWrite(LED_BUILTIN, LOW);   // LED off
  delay(500);
}`;


// =============================================================================
// BLOCK 2: PINOUT DATA
// Used by renderPinout() to build the pin reference grid.
// type: "power" | "gnd" | "adc" | "special" | "gpio"
// =============================================================================
const PINS = [
  { num:"3V3",  func:"3.3V Power",   type:"power"   },
  { num:"GND",  func:"Ground",       type:"gnd"     },
  { num:"EN",   func:"Chip Enable",  type:"special" },
  { num:"VP",   func:"ADC0 / IN+",   type:"adc"     },
  { num:"VN",   func:"ADC3 / IN−",   type:"adc"     },
  { num:"D34",  func:"ADC6 Input",   type:"adc"     },
  { num:"D35",  func:"ADC7 Input",   type:"adc"     },
  { num:"D32",  func:"ADC4/DAC",     type:"adc"     },
  { num:"D33",  func:"ADC5",         type:"adc"     },
  { num:"D25",  func:"DAC1 / ADC18", type:"special" },
  { num:"D26",  func:"DAC2 / ADC19", type:"special" },
  { num:"D27",  func:"ADC17",        type:"gpio"    },
  { num:"D14",  func:"ADC16 / HSPI", type:"gpio"    },
  { num:"D12",  func:"ADC15 / HSPI", type:"gpio"    },
  { num:"D13",  func:"ADC14 / HSPI", type:"gpio"    },
  { num:"VIN",  func:"5V Input",     type:"power"   },
  { num:"GND",  func:"Ground",       type:"gnd"     },
  { num:"D15",  func:"ADC13 / HSPI", type:"gpio"    },
  { num:"D2",   func:"LED/ADC12",    type:"special" },
  { num:"D4",   func:"ADC10",        type:"adc"     },
  { num:"RX2",  func:"UART2 RX",     type:"special" },
  { num:"TX2",  func:"UART2 TX",     type:"special" },
  { num:"D5",   func:"SS / PWM",     type:"gpio"    },
  { num:"D18",  func:"SCK / SPI",    type:"gpio"    },
  { num:"D19",  func:"MISO / SPI",   type:"gpio"    },
  { num:"D21",  func:"I2C SDA",      type:"special" },
  { num:"RX0",  func:"UART0 RX",     type:"special" },
  { num:"TX0",  func:"UART0 TX",     type:"special" },
  { num:"D22",  func:"I2C SCL",      type:"special" },
  { num:"D23",  func:"MOSI / SPI",   type:"gpio"    },
];


// =============================================================================
// BLOCK 3: APP STATE
// Single source of truth for all mutable values.
// Never directly mutate DOM — update state, then call a render function.
// =============================================================================
const state = {
  outputTab:    "serial",   // "serial" | "compiler"
  serialLines:  [],         // { text, cls }[] for serial monitor
  compilerLines:[],         // { text, cls }[] for compiler output
  autoScroll:   true,       // whether serial output auto-scrolls
  isLoading:    false,      // blocks buttons during compile/upload
  activeBoard:  "esp32",    // board ID (matches /api/boards keys)
  sketchName:   "sketch",   // base name (no .ino) of current sketch
};


// =============================================================================
// BLOCK 4: DOM HELPERS
// Lazy getters — we call document.getElementById only once per element.
// Using a function (not a cached var) so this always works even after
// partial DOM changes without a full page reload.
// =============================================================================
const $ = id => document.getElementById(id);
const D = {
  editor:         () => $("code-editor"),
  lineNums:       () => $("line-nums"),
  serialOut:      () => $("serial-output"),
  loadingOverlay: () => $("loading-overlay"),
  loadingBar:     () => $("loading-bar"),
  loadingText:    () => $("loading-text"),
  statusMsg:      () => $("status-msg"),
  cursorPos:      () => $("cursor-pos"),
  sketchSelectL:  () => $("board-select"),      // left pane board sel
  sketchSelectR:  () => $("tb-board-sel"),      // right pane board sel
  exampleSel:     () => $("sketch-select") || document.querySelector(".tb-example-sel"),
  pinoutGrid:     () => $("pinout-grid"),
  tabSerial:      () => $("tab-serial"),
  tabCompiler:    () => $("tab-compiler"),
  btnAutoscroll:  () => $("btn-autoscroll"),
  btnVerify:      () => $("btn-verify"),
  btnUpload:      () => $("btn-upload"),
  kbModal:        () => $("kb-modal"),
  winLeft:        () => $("win-left"),
  winRight:       () => $("win-right"),
  divider:        () => $("divider"),
  // Sketch name displays
  sketchTitleBar: () => $("sketch-title-bar"),
  sbFilename:     () => $("sb-filename"),
  editorTabName:  () => $("editor-tab-name"),
  sbBoardTag:     () => $("sb-board-tag"),
  menuBoardPort:  () => $("menu-board-port"),
  statusbarBoard: () => $("statusbar-board"),
  boardPillName:  () => $("board-pill-name"),
  sbLibs:         () => $("sb-libs"),
};


// =============================================================================
// BLOCK 5: INITIALIZATION
// Everything that must run once the DOM is fully parsed.
// =============================================================================
document.addEventListener("DOMContentLoaded", () => {
  // 1. Populate editor with default sketch
  D.editor().value = DEFAULT_SKETCH;
  updateLineNums();

  // 2. Render pinout grid (even if section is hidden — it's already in DOM)
  renderPinout();

  // 3. Load sketch list into the dropdown from Flask
  loadSketchList();

  // 4. Initialize output panel empty state
  renderOutput();

  // 5. Set initial cursor position
  updateCursorPos();

  console.log("[ESP32Studio] Ready ✓");
});


// =============================================================================
// BLOCK 6: EDITOR
// Line numbers, cursor tracking, Tab key intercept, scroll sync.
// =============================================================================

// Rebuild the gutter on every keystroke.
// Called by oninput on the textarea.
function updateLineNums() {
  const ed    = D.editor();
  const lines = ed.value.split("\n");
  const gutter = D.lineNums();

  gutter.innerHTML = lines
    .map((_, i) => `<div class="gutter-line">${i + 1}</div>`)
    .join("");
}

// Show "Ln X, Col Y" in status bar.
// Called on every keyup and mouseup.
function updateCursorPos() {
  const ed     = D.editor();
  const before = ed.value.substring(0, ed.selectionStart);
  const ln     = before.split("\n").length;
  const col    = before.split("\n").pop().length + 1;
  if (D.cursorPos()) D.cursorPos().textContent = `Ln ${ln}, Col ${col}`;
}

// Combined handler: oninput fires on every character typed
function onEditorInput() {
  updateLineNums();
  updateCursorPos();
}

// Keep gutter scroll in sync with editor scroll.
// Called by onscroll on the textarea.
function syncScroll() {
  D.lineNums().scrollTop = D.editor().scrollTop;
}

// Intercept Tab key to insert 2 spaces instead of shifting browser focus.
// Called by onkeydown on the textarea.
function handleEditorKey(e) {
  if (e.key !== "Tab") return;
  e.preventDefault();
  const ed    = D.editor();
  const start = ed.selectionStart;
  const end   = ed.selectionEnd;
  ed.value    = ed.value.substring(0, start) + "  " + ed.value.substring(end);
  ed.selectionStart = ed.selectionEnd = start + 2;
  updateLineNums();
}


// =============================================================================
// BLOCK 7: LEFT PANE — SECTION SWITCHER + CARD FILTER
// =============================================================================

// Show one of: "project" | "pinout" | "docs" | "simulator"
// Hides all others, updates nav tab active state.
function showSection(name) {
  // Hide all sections
  document.querySelectorAll(".wl-section").forEach(el => el.classList.add("hidden"));
  // Show target
  const target = $(`section-${name}`);
  if (target) target.classList.remove("hidden");

  // Update nav tab active state
  document.querySelectorAll(".wl-navtab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.sec === name);
  });
}

// Filter example cards by category.
// "all" shows everything; other values hide non-matching cards.
function filterCards(cat, btn) {
  // Update filter button active state
  document.querySelectorAll(".fbtn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");

  // Show/hide cards
  document.querySelectorAll(".wl-card[data-cat]").forEach(card => {
    const match = cat === "all" || card.dataset.cat === cat;
    card.style.display = match ? "" : "none";
  });
}


// =============================================================================
// BLOCK 8: PINOUT GRID
// Builds the ESP32 pin reference cards from the PINS array.
// =============================================================================
function renderPinout() {
  const grid = D.pinoutGrid();
  if (!grid) return;
  grid.innerHTML = PINS.map(p => `
    <div class="pin-item ${p.type}" title="${p.func}">
      <div class="pin-num">${p.num}</div>
      <div class="pin-func">${p.func}</div>
    </div>
  `).join("");
}


// =============================================================================
// BLOCK 9: OUTPUT PANEL
// Serial monitor + compiler output tabs.
// =============================================================================

// Switch between "serial" and "compiler" output tabs
function setOutputTab(tab) {
  state.outputTab = tab;
  D.tabSerial().classList.toggle("active",   tab === "serial");
  D.tabCompiler().classList.toggle("active", tab === "compiler");
  renderOutput();
}

// Render current tab's content into the console log div
function renderOutput() {
  const el    = D.serialOut();
  const lines = state.outputTab === "serial"
    ? state.serialLines
    : state.compilerLines;

  if (lines.length === 0) {
    const placeholder = state.outputTab === "serial"
      ? "Serial monitor empty — upload a sketch to see output."
      : "No compiler output yet — click Verify to compile.";
    el.innerHTML = `<div class="log-dim">${placeholder}</div>`;
    return;
  }

  el.innerHTML = lines
    .map(l => `<div class="${l.cls}">${escHtml(l.text)}</div>`)
    .join("");

  if (state.autoScroll) el.scrollTop = el.scrollHeight;
}

// Helper: push a line into the serial log
function logSerial(text, cls = "log-plain") {
  state.serialLines.push({ text, cls });
}

// Helper: push a line into the compiler log
function logCompiler(text, cls = "log-plain") {
  state.compilerLines.push({ text, cls });
}

// Clear the currently visible tab
function clearOutput() {
  if (state.outputTab === "serial") state.serialLines = [];
  else state.compilerLines = [];
  renderOutput();
}

// Toggle auto-scroll on/off
function toggleAutoScroll() {
  state.autoScroll = !state.autoScroll;
  const btn = D.btnAutoscroll();
  if (!btn) return;
  btn.classList.toggle("on", state.autoScroll);
  btn.textContent = state.autoScroll ? "↓ Auto" : "↓ Off";
}


// =============================================================================
// BLOCK 10: API — VERIFY (COMPILE)
// POST /api/compile with current code.
// Shows result in Compiler Output tab.
// =============================================================================
async function verifyCode() {
  if (state.isLoading) return;

  const code = D.editor().value;
  state.compilerLines = [];
  logCompiler(`Compiling sketch for ${state.activeBoard}...`, "log-info");
  setOutputTab("compiler");
  showLoading("Compiling sketch...", 55);
  setBtns(false);

  try {
    const res  = await fetch("/api/compile", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ code, board: state.activeBoard })
    });
    const data = await res.json();

    hideLoading();
    setBtns(true);

    if (data.status === "success") {
      // Show any warnings first
      (data.warnings || []).forEach(w => logCompiler("⚠  " + w, "log-warn"));
      logCompiler("✓  Compilation successful — no errors", "log-ok");
      // Show binary size info lines
      (data.message || "").split("\n").forEach(l => { if (l) logCompiler(l, "log-dim"); });
      setStatus("Compiled OK", true);
    } else {
      (data.errors   || []).forEach(e => logCompiler("✗  " + e, "log-err"));
      (data.warnings || []).forEach(w => logCompiler("⚠  " + w, "log-warn"));
      setStatus("Compile error", false);
    }
  } catch (err) {
    hideLoading();
    setBtns(true);
    logCompiler("Network error: " + err.message, "log-err");
    logCompiler("Is the Flask server running?  →  python app.py", "log-dim");
    setStatus("Network error", false);
  }

  renderOutput();
}


// =============================================================================
// BLOCK 11: API — UPLOAD
// POST /api/upload with current code.
// Shows simulated esptool.py output in Serial Monitor tab.
// =============================================================================
async function uploadCode() {
  if (state.isLoading) return;

  const code = D.editor().value;
  state.serialLines = [];
  setOutputTab("serial");
  showLoading("Uploading to board...", 100);
  setBtns(false);

  try {
    const res  = await fetch("/api/upload", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ code, board: state.activeBoard })
    });
    const data = await res.json();

    hideLoading();
    setBtns(true);

    if (data.status === "success") {
      // Colour-code each line based on content keywords
      (data.serial || []).forEach(line => {
        let cls = "log-plain";
        if      (line.includes("✓") || line.includes("complete") || line.includes("Ready")) cls = "log-ok";
        else if (line.includes("Error") || line.includes("Failed"))                          cls = "log-err";
        else if (line.includes("Connecting") || line.includes("esptool") ||
                 line.includes("Chip")       || line.includes("Flash"))                      cls = "log-info";
        else if (line.includes("warning") || line.includes("⚠"))                            cls = "log-warn";
        logSerial(line, cls);
      });
      setStatus("Uploaded ✓", true);
    } else {
      logSerial(data.message, "log-err");
      setStatus("Upload failed", false);
    }
  } catch (err) {
    hideLoading();
    setBtns(true);
    logSerial("Network error: " + err.message, "log-err");
    setStatus("Network error", false);
  }

  renderOutput();
}


// =============================================================================
// BLOCK 12: SKETCH LOADER
// Populates dropdown from /api/sketches.
// Loads code from /api/sketch/<n>.
// =============================================================================

// Fetch all sketch metadata and populate the "Open Example..." dropdown
async function loadSketchList() {
  try {
    const res  = await fetch("/api/sketches");
    const list = await res.json();

    // Find the example select — there may be two (left + right toolbar)
    document.querySelectorAll(".tb-example-sel, #sketch-select").forEach(sel => {
      Object.entries(list).forEach(([id, meta]) => {
        const opt = document.createElement("option");
        opt.value       = id;
        opt.textContent = `${meta.category}  →  ${meta.title}`;
        sel.appendChild(opt);
      });
    });
  } catch (err) {
    console.warn("[ESP32Studio] Could not load sketch list:", err.message);
  }
}

// Called when user picks from the "Open Example..." dropdown
function onSketchSelect(value) {
  if (!value) return;
  loadSketch(value);
  // Reset all dropdowns back to placeholder
  document.querySelectorAll(".tb-example-sel").forEach(s => s.value = "");
}

// Load a named sketch into the editor
async function loadSketch(name) {
  try {
    const res  = await fetch(`/api/sketch/${name}`);
    const data = await res.json();
    if (data.error) { console.warn("Sketch not found:", name); return; }

    // Put code in editor
    D.editor().value = data.code;
    updateLineNums();

    // Update file name everywhere
    const fname = `${name}.ino`;
    state.sketchName = name;
    if (D.sketchTitleBar()) D.sketchTitleBar().textContent = fname;
    if (D.sbFilename())     D.sbFilename().textContent     = fname;
    if (D.editorTabName())  D.editorTabName().textContent  = fname;

    // Update library hints in sidebar
    updateLibraryHints(data.code);

    D.editor().focus();
    setStatus(`Loaded: ${data.title}`, true);

    // Clear old output when loading new sketch
    state.serialLines  = [];
    state.compilerLines = [];
    renderOutput();

  } catch (err) {
    console.error("[ESP32Studio] Load sketch failed:", err);
  }
}

// Called from the example cards in the left pane
function loadAndFocus(name) {
  loadSketch(name);
}

// Detect #include directives and show them in the sidebar library list
function updateLibraryHints(code) {
  const libs = D.sbLibs();
  if (!libs) return;

  // Match all #include <LibraryName.h> lines
  const includes = [...code.matchAll(/#include\s*[<"]([\w.]+)[>"]/g)]
    .map(m => m[1])
    .filter(l => l !== "Arduino.h"); // filter out the obvious one

  if (includes.length === 0) {
    libs.innerHTML = `<span class="sb-empty">No external libraries</span>`;
    return;
  }

  libs.innerHTML = includes
    .map(l => `<div class="sb-file"><span style="color:var(--a-teal);font-size:11px">#</span> ${l}</div>`)
    .join("");
}

// Create a blank new sketch
function newSketch() {
  D.editor().value = `// New Sketch\n\nvoid setup() {\n  Serial.begin(115200);\n}\n\nvoid loop() {\n  // code here\n  delay(100);\n}`;
  updateLineNums();
  D.editor().focus();
  state.sketchName = "sketch";
  const fname = "sketch.ino";
  if (D.sketchTitleBar()) D.sketchTitleBar().textContent = fname;
  if (D.sbFilename())     D.sbFilename().textContent     = fname;
  if (D.editorTabName())  D.editorTabName().textContent  = fname;
  if (D.sbLibs())         D.sbLibs().innerHTML           = `<span class="sb-empty">No external libraries</span>`;
  state.serialLines = [];
  state.compilerLines = [];
  renderOutput();
  setStatus("New sketch", true);
}


// =============================================================================
// BLOCK 13: BOARD SELECTOR
// =============================================================================
async function onBoardChange(boardId) {
  state.activeBoard = boardId;

  // Sync all board dropdowns to the same value
  ["board-select", "tb-board-sel"].forEach(id => {
    const el = $(id);
    if (el) el.value = boardId;
  });

  // Update board name displays
  try {
    const res   = await fetch("/api/boards");
    const data  = await res.json();
    const board = data.boards.find(b => b.id === boardId);
    if (!board) return;

    if (D.boardPillName())   D.boardPillName().textContent  = board.name.split(" ")[0];
    if (D.sbBoardTag())      D.sbBoardTag().textContent     = boardId;
    if (D.menuBoardPort())   D.menuBoardPort().textContent  = `${board.name}  ·  /dev/ttyUSB0`;
    if (D.statusbarBoard())  D.statusbarBoard().textContent = board.name;
  } catch (_) {}
}


// =============================================================================
// BLOCK 14: PANE RESIZER
// Drag the divider between win-left and win-right to resize.
// =============================================================================
(function initResizer() {
  const divider  = D.divider();
  const left     = D.winLeft();
  const right    = D.winRight();
  let dragging   = false;

  // Mousedown on divider → start drag
  divider.addEventListener("mousedown", e => {
    dragging = true;
    divider.classList.add("dragging");
    document.body.style.cursor     = "col-resize";
    document.body.style.userSelect = "none";
    e.preventDefault();
  });

  // Mousemove → recompute widths
  document.addEventListener("mousemove", e => {
    if (!dragging) return;

    const appRect = document.querySelector(".app-shell").getBoundingClientRect();
    const x       = e.clientX - appRect.left;
    const total   = appRect.width - 5; // subtract divider width

    // Clamp: left pane minimum 25%, max 75%
    const pct = Math.min(Math.max(x / total * 100, 25), 75);

    left.style.flex  = "none";
    left.style.width = pct + "%";
    right.style.flex = "1";
  });

  // Mouseup → end drag
  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    divider.classList.remove("dragging");
    document.body.style.cursor     = "";
    document.body.style.userSelect = "";
  });
})();


// =============================================================================
// BLOCK 15: KEYBOARD SHORTCUTS
// =============================================================================
document.addEventListener("keydown", e => {
  const ctrl = e.ctrlKey || e.metaKey;

  // Ctrl+R → Verify
  if (ctrl && (e.key === "r" || e.key === "Enter")) { e.preventDefault(); verifyCode();  }
  // Ctrl+U → Upload
  if (ctrl && e.key === "u")                        { e.preventDefault(); uploadCode();  }
  // Ctrl+N → New sketch
  if (ctrl && e.key === "n")                        { e.preventDefault(); newSketch();   }
  // ? → Toggle shortcuts modal
  if (e.key === "?" && !ctrl)                       { toggleKbModal(); }
  // Escape → Close modal
  if (e.key === "Escape")                           { closeModal(); }
});

function toggleKbModal() {
  const m = D.kbModal();
  if (m) m.classList.toggle("open");
}
function closeModal() {
  const m = D.kbModal();
  if (m) m.classList.remove("open");
}


// =============================================================================
// BLOCK 16: LOADING OVERLAY HELPERS
// =============================================================================

// Show the compile/upload overlay.
// pct: how far to fill the progress bar (0–100)
function showLoading(text, pct = 50) {
  state.isLoading = true;

  const overlay = D.loadingOverlay();
  const bar     = D.loadingBar();
  const txt     = D.loadingText();

  if (!overlay) return;

  // Activate the overlay — CSS class "active" changes display:none → display:flex
  overlay.classList.add("active");

  if (txt) txt.textContent = text;

  // Animate progress bar from 0 to pct
  if (bar) {
    bar.style.width = "0%";
    requestAnimationFrame(() => {
      setTimeout(() => { bar.style.width = pct + "%"; }, 30);
    });
  }
}

// Hide overlay — fill bar to 100% first for a satisfying completion feel
function hideLoading() {
  state.isLoading = false;
  const overlay = D.loadingOverlay();
  const bar     = D.loadingBar();

  if (bar) bar.style.width = "100%";

  setTimeout(() => {
    if (overlay) overlay.classList.remove("active");
    if (bar)     bar.style.width = "0%";
  }, 250); // slight delay so user sees 100%
}

// Enable or disable the Verify + Upload buttons
function setBtns(on) {
  [D.btnVerify(), D.btnUpload()].forEach(btn => {
    if (btn) btn.disabled = !on;
  });
}


// =============================================================================
// BLOCK 17: UTILITIES
// =============================================================================

// Update the IDE status bar text + colour
// ok=true → bright text (success), ok=false → red (error)
function setStatus(text, ok = true) {
  const el = D.statusMsg();
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? "rgba(255,255,255,0.95)" : "#F44747";
}

// Escape HTML so log lines with < > & display safely
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}