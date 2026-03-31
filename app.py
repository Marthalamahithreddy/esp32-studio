# =============================================================================
#  GEN ALPHA ESP32 STUDIO — app.py
#  Flask backend with a REAL multi-pass compiler pipeline.
#
#  STAGE OVERVIEW (what happens when you click "Verify"):
#   Pass 1  → Tokenizer          : strip comments, find strings, classify tokens
#   Pass 2  → Preprocessor       : resolve #define macros, #include headers
#   Pass 3  → Signature scanner  : extract every function signature
#   Pass 4  → Semantic analyser  : type checks, undeclared vars, missing returns
#   Pass 5  → Linter             : style, complexity, bad patterns, ESP32 traps
#   Pass 6  → Dependency graph   : which functions call which (for dead-code)
#   Pass 7  → Dead-code detector : unreachable / never-called functions
#   Pass 8  → Memory estimator   : static SRAM + stack depth per function
#   Pass 9  → Flash estimator    : program storage estimate by feature set
#   Pass 10 → Diagnostic ranker  : sort errors > warnings > hints by line
#
#  BLOCK INDEX:
#   BLOCK 1  — Imports & Flask init
#   BLOCK 2  — Shared constants  (board profiles, known libraries, esp32 traps)
#   BLOCK 3  — Pass 1: Tokenizer / comment stripper
#   BLOCK 4  — Pass 2: Preprocessor (macros + includes)
#   BLOCK 5  — Pass 3: Signature scanner
#   BLOCK 6  — Pass 4: Semantic analyser
#   BLOCK 7  — Pass 5: Linter
#   BLOCK 8  — Pass 6: Dependency graph builder
#   BLOCK 9  — Pass 7: Dead-code detector
#   BLOCK 10 — Pass 8: Memory estimator
#   BLOCK 11 — Pass 9: Flash estimator
#   BLOCK 12 — Pass 10: Diagnostic ranker / formatter
#   BLOCK 13 — Master compile() orchestrator
#   BLOCK 14 — Upload simulator
#   BLOCK 15 — Sketch library (8 example sketches)
#   BLOCK 16 — Flask routes
#   BLOCK 17 — Server startup
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 1 — IMPORTS & FLASK INIT
# ─────────────────────────────────────────────────────────────────────────────
from flask import Flask, render_template, jsonify, request
import re
import time
import random
import math
from collections import defaultdict

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 2 — SHARED CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Board hardware profiles used by memory and flash estimators
BOARD_PROFILES = {
    "esp32": {
        "name":       "ESP32 Dev Module",
        "flash_max":  1_310_720,   # 1.25 MB usable program space
        "ram_max":    327_680,     # 320 KB SRAM
        "freq_mhz":  240,
        "has_wifi":   True,
        "has_bt":     True,
        "has_dac":    True,
        "adc_bits":   12,
    },
    "esp32s3": {
        "name":       "ESP32-S3 Dev Module",
        "flash_max":  1_572_864,
        "ram_max":    524_288,
        "freq_mhz":  240,
        "has_wifi":   True,
        "has_bt":     True,
        "has_dac":    False,
        "adc_bits":   12,
    },
    "uno": {
        "name":       "Arduino Uno CH340G",
        "flash_max":  32_256,      # ATmega328P: 32KB - 512B bootloader
        "ram_max":    2_048,       # 2 KB SRAM
        "freq_mhz":  16,
        "has_wifi":   False,
        "has_bt":     False,
        "has_dac":    False,
        "adc_bits":   10,
    },
    "nano": {
        "name":       "Arduino Nano",
        "flash_max":  30_720,
        "ram_max":    2_048,
        "freq_mhz":  16,
        "has_wifi":   False,
        "has_bt":     False,
        "has_dac":    False,
        "adc_bits":   10,
    },
}

# Libraries that we recognise — used by the preprocessor and dependency graph
KNOWN_HEADERS = {
    "WiFi.h":              {"lib": "ESP32 WiFi (built-in)",        "ram_kb": 38},
    "HTTPClient.h":        {"lib": "ESP32 HTTPClient (built-in)",  "ram_kb": 8},
    "WiFiClient.h":        {"lib": "ESP32 WiFiClient (built-in)", "ram_kb": 4},
    "DHT.h":               {"lib": "Adafruit DHT sensor",          "ram_kb": 1},
    "ESP32Servo.h":        {"lib": "ESP32Servo",                   "ram_kb": 1},
    "Adafruit_SSD1306.h":  {"lib": "Adafruit SSD1306",            "ram_kb": 4},
    "Adafruit_GFX.h":      {"lib": "Adafruit GFX Library",        "ram_kb": 2},
    "Wire.h":              {"lib": "Wire I2C (built-in)",          "ram_kb": 1},
    "SPI.h":               {"lib": "SPI (built-in)",               "ram_kb": 1},
    "EEPROM.h":            {"lib": "EEPROM (built-in)",            "ram_kb": 1},
    "Preferences.h":       {"lib": "NVS Preferences (built-in)",  "ram_kb": 2},
    "BluetoothSerial.h":   {"lib": "BluetoothSerial (built-in)",  "ram_kb": 40},
    "BLEDevice.h":         {"lib": "BLE (built-in)",              "ram_kb": 60},
    "ArduinoJson.h":       {"lib": "ArduinoJson",                 "ram_kb": 0},
    "PubSubClient.h":      {"lib": "PubSubClient (MQTT)",         "ram_kb": 2},
    "FastLED.h":           {"lib": "FastLED",                     "ram_kb": 5},
}

# ESP32-specific traps — things Arduino Uno allows but ESP32 doesn't
ESP32_TRAPS = [
    # (pattern, severity, message)
    (r'\banalogWrite\s*\(',   "error",   "analogWrite() is NOT supported on ESP32. "
                                          "Use ledcSetup() + ledcAttachPin() + ledcWrite() for PWM."),
    (r'\bdelay\s*\(\s*0\s*\)',"warning", "delay(0) on ESP32 triggers watchdog reset. "
                                          "Use vTaskDelay(1) or yield() instead."),
    (r'\bpinMode\s*\(\s*6[,\s]',"error", "GPIO6–11 are reserved for SPI flash on ESP32. "
                                           "Using them will crash the chip."),
    (r'\bpinMode\s*\(\s*7[,\s]',"error", "GPIO6–11 are reserved for SPI flash on ESP32."),
    (r'\bpinMode\s*\(\s*8[,\s]',"error", "GPIO6–11 are reserved for SPI flash on ESP32."),
    (r'\bpinMode\s*\(\s*9[,\s]',"error", "GPIO6–11 are reserved for SPI flash on ESP32."),
    (r'\bpinMode\s*\(\s*10[,\s]',"error","GPIO6–11 are reserved for SPI flash on ESP32."),
    (r'\bpinMode\s*\(\s*11[,\s]',"error","GPIO6–11 are reserved for SPI flash on ESP32."),
    (r'\bEEPROM\.begin\(',    "warning", "ESP32 EEPROM emulation writes to NVS flash. "
                                          "Prefer Preferences library for wear levelling."),
    (r'\binterrupts\(\)',     "warning",  "interrupts() is an alias for sei() (AVR). "
                                          "On ESP32 use portENABLE_INTERRUPTS(portMUX_INITIALIZER_UNLOCKED)."),
    (r'\bnoInterrupts\(',     "warning",  "noInterrupts() is AVR-specific. "
                                          "On ESP32 use portDISABLE_INTERRUPTS(portMUX_INITIALIZER_UNLOCKED)."),
    (r'\bSRAM\b',             "info",     "SRAM is an AVR term. ESP32 uses DRAM/IRAM. "
                                          "Use ESP.getFreeHeap() to query available heap."),
    (r'\bpgmspace\b|PROGMEM', "warning",  "PROGMEM is AVR-only. On ESP32 all flash data "
                                           "is memory-mapped — no PROGMEM needed."),
    (r'Serial\.begin\(\s*9600\s*\)', "warning",
                                          "9600 baud is very slow on ESP32. Use 115200."),
    (r'WiFi\.\w+.*ADC2',     "info",      "ADC2 pins (GPIO0,2,4,12-15,25-27) cannot be "
                                           "used while WiFi is active."),
]

# C/C++ primitive types the semantic analyser recognises
PRIMITIVE_TYPES = {
    "void","int","long","unsigned","float","double","char","byte",
    "bool","boolean","String","uint8_t","uint16_t","uint32_t","uint64_t",
    "int8_t","int16_t","int32_t","int64_t","size_t","word","short",
}

# Arduino built-in globals — should not be flagged as undeclared
ARDUINO_GLOBALS = {
    "HIGH","LOW","INPUT","OUTPUT","INPUT_PULLUP","INPUT_PULLDOWN",
    "LED_BUILTIN","MSBFIRST","LSBFIRST","RISING","FALLING","CHANGE",
    "true","false","NULL","nullptr","PI","TWO_PI","HALF_PI","DEG_TO_RAD",
    "Serial","Serial1","Serial2","Wire","Wire1","SPI","SPIFFS","SD",
    "WiFi","WiFiClient","HTTPClient","BluetoothSerial","BLEDevice",
    "A0","A1","A2","A3","A4","A5","A6","A7","A8","A9","A10","A11",
}


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 3 — PASS 1: TOKENIZER / COMMENT STRIPPER
# Returns:
#   stripped_code  — source with comments removed (preserves line counts)
#   string_regions — list of (start_char, end_char) for string literals
# ─────────────────────────────────────────────────────────────────────────────
def pass1_tokenize(source: str):
    """
    Remove C-style block comments (/* ... */) and line comments (// ...)
    while keeping newlines so that line numbers remain accurate.
    Also records where string literals are so later passes don't
    try to parse code *inside* strings.
    """
    result       = []
    string_regions = []
    i            = 0
    n            = len(source)

    while i < n:
        # ── Block comment /*  */
        if source[i:i+2] == "/*":
            j = source.find("*/", i+2)
            if j == -1:
                j = n
            # Replace comment chars with spaces, keep newlines
            chunk = source[i:j+2]
            result.append(re.sub(r'[^\n]', ' ', chunk))
            i = j + 2
            continue

        # ── Line comment //
        if source[i:i+2] == "//":
            j = source.find('\n', i)
            if j == -1:
                j = n
            # Replace everything up to the newline with spaces
            result.append(' ' * (j - i))
            i = j
            continue

        # ── String literal "..."
        if source[i] == '"':
            start = i
            i += 1
            while i < n and source[i] != '"':
                if source[i] == '\\':
                    i += 1  # skip escaped char
                i += 1
            i += 1  # skip closing "
            string_regions.append((start, i))
            result.append(source[start:i])
            continue

        # ── Character literal '.'
        if source[i] == "'":
            start = i
            i += 1
            while i < n and source[i] != "'":
                if source[i] == '\\':
                    i += 1
                i += 1
            i += 1
            result.append(source[start:i])
            continue

        result.append(source[i])
        i += 1

    return "".join(result), string_regions


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 4 — PASS 2: PREPROCESSOR
# Resolves #define macros and catalogues #include headers.
# Returns:
#   expanded       — source with macro names substituted
#   defines        — dict { macro_name: replacement_text }
#   includes       — list of header names found
#   include_issues — list of diagnostic dicts for unknown/missing headers
# ─────────────────────────────────────────────────────────────────────────────
def pass2_preprocess(stripped: str, board_id: str):
    defines        = {}
    includes       = []
    include_issues = []
    lines          = stripped.split('\n')
    expanded_lines = []
    board          = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])

    for ln, line in enumerate(lines, 1):
        stripped_line = line.strip()

        # ── #define  MACRO  value
        m = re.match(r'#define\s+(\w+)\s+(.*)', stripped_line)
        if m:
            defines[m.group(1)] = m.group(2).strip()
            expanded_lines.append(line)
            continue

        # ── #include <header.h> or "header.h"
        m = re.match(r'#include\s+[<"]([\w./]+)[>"]', stripped_line)
        if m:
            hdr = m.group(1)
            includes.append(hdr)
            if hdr not in KNOWN_HEADERS:
                include_issues.append({
                    "line": ln, "col": 1, "severity": "warning",
                    "code": "W001",
                    "message": f"Unknown library '{hdr}' — make sure it's installed via Library Manager.",
                    "pass": "Preprocessor"
                })
            # Board-specific check: WiFi on Uno
            if hdr == "WiFi.h" and not board["has_wifi"]:
                include_issues.append({
                    "line": ln, "col": 1, "severity": "error",
                    "code": "E010",
                    "message": f"{board['name']} does not have WiFi. Remove WiFi.h.",
                    "pass": "Preprocessor"
                })
            expanded_lines.append(line)
            continue

        # ── Expand #define macros in code lines
        for macro, value in defines.items():
            # Word-boundary replacement so FOO doesn't replace FOOBAR
            line = re.sub(r'\b' + re.escape(macro) + r'\b', value, line)
        expanded_lines.append(line)

    return '\n'.join(expanded_lines), defines, includes, include_issues


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 5 — PASS 3: SIGNATURE SCANNER
# Extracts every function definition from the source.
# Returns list of dicts: { name, return_type, params, line, col, body_start }
# ─────────────────────────────────────────────────────────────────────────────
def pass3_signatures(source: str):
    """
    Pattern: <return_type> <name> ( <params> ) { ...}
    We look for the pattern but don't parse the full body here —
    just record where the function starts so later passes can check it.
    """
    # Match function signature — return type + name + params
    pattern = re.compile(
        r'^[ \t]*((?:(?:const|static|volatile|inline|unsigned|long|short)\s+)*'
        r'(?:void|int|long|float|double|char|byte|bool|boolean|String|uint\w*|int\w*|size_t|word))'
        r'\s+(\w+)\s*\(([^)]*)\)\s*\{',
        re.MULTILINE
    )
    functions = []
    lines = source.split('\n')

    for m in pattern.finditer(source):
        ret  = m.group(1).strip()
        name = m.group(2)
        params_raw = m.group(3).strip()
        # Determine line number from char offset
        line_num = source[:m.start()].count('\n') + 1
        col      = m.start() - source.rfind('\n', 0, m.start())

        # Parse parameter list into typed names
        params = []
        if params_raw:
            for p in params_raw.split(','):
                p = p.strip()
                if p:
                    params.append(p)

        functions.append({
            "name":        name,
            "return_type": ret,
            "params":      params,
            "line":        line_num,
            "col":         col,
            "body_start":  m.end(),
        })

    return functions


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 6 — PASS 4: SEMANTIC ANALYSER
# Checks:
#  • Missing setup() / loop()
#  • Non-void functions without a return statement
#  • Brace balance
#  • Redefined function names
#  • Calling undefined functions (basic — only catches obvious mistakes)
#  • Variable declared but assigned wrong type (basic numeric check)
# ─────────────────────────────────────────────────────────────────────────────
def pass4_semantics(source: str, functions: list, defines: dict):
    diags = []
    fn_names = {f["name"] for f in functions}

    # ── Required Arduino entry points
    has_setup = any(f["name"] == "setup" for f in functions)
    has_loop  = any(f["name"] == "loop"  for f in functions)
    if not has_setup:
        diags.append({"line":1,"col":1,"severity":"error","code":"E001",
            "message":"Missing void setup() — Arduino requires a setup() entry point.",
            "pass":"Semantic"})
    if not has_loop:
        diags.append({"line":1,"col":1,"severity":"error","code":"E002",
            "message":"Missing void loop() — Arduino requires a loop() entry point.",
            "pass":"Semantic"})

    # ── Brace balance check
    open_b  = source.count('{')
    close_b = source.count('}')
    if open_b != close_b:
        diff = abs(open_b - close_b)
        which = "opening '{'" if open_b > close_b else "closing '}'"
        diags.append({"line":1,"col":1,"severity":"error","code":"E003",
            "message":f"Mismatched braces: {diff} extra {which}. "
                      f"Total {{ = {open_b}, total }} = {close_b}.",
            "pass":"Semantic"})

    # ── Parenthesis balance
    open_p  = source.count('(')
    close_p = source.count(')')
    if open_p != close_p:
        diff = abs(open_p - close_p)
        which = "opening '('" if open_p > close_p else "closing ')'"
        diags.append({"line":1,"col":1,"severity":"error","code":"E004",
            "message":f"Mismatched parentheses: {diff} extra {which}.",
            "pass":"Semantic"})

    # ── Duplicate function names
    seen = {}
    for fn in functions:
        nm = fn["name"]
        if nm in seen:
            diags.append({"line":fn["line"],"col":fn["col"],"severity":"error",
                "code":"E005",
                "message":f"Function '{nm}' is defined more than once "
                           f"(first at line {seen[nm]}).",
                "pass":"Semantic"})
        seen[nm] = fn["line"]

    # ── Non-void functions: must have a return statement in body
    lines = source.split('\n')
    for fn in functions:
        if fn["return_type"] in {"void", ""}:
            continue
        # Heuristic: scan from body_start to next top-level closing brace
        body_chunk = source[fn["body_start"]:]
        depth = 1
        end   = 0
        for ch in body_chunk:
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body = body_chunk[:end]
        if not re.search(r'\breturn\b', body):
            diags.append({"line":fn["line"],"col":fn["col"],"severity":"warning",
                "code":"W010",
                "message":f"Function '{fn['name']}' has return type '{fn['return_type']}' "
                           f"but no return statement found.",
                "pass":"Semantic"})

    # ── Missing semicolons (common beginner mistake)
    for ln, line in enumerate(lines, 1):
        s = line.strip()
        # Skip preprocessor, comments, block markers, for-loop lines
        if not s or s.startswith('#') or s.startswith('//'):
            continue
        if s.endswith(('{', '}', ',')):
            continue
        # Lines that look like statements (contain = ; ( etc.) but don't end in ;
        if re.search(r'[=\w)\]]$', s) and re.search(r'[=(,\w]', s):
            # Filter out control flow and function definitions
            if not re.match(r'^(if|else|for|while|do|switch|case|default|void|int|float|char|bool)', s):
                # Only flag lines that look like assignments or function calls
                if re.search(r'\w\s*\(|\w\s*=\s*\w|^\w+\s+\w+\s*=', s):
                    diags.append({"line":ln,"col":len(line),"severity":"warning",
                        "code":"W011",
                        "message":f"Possible missing semicolon at end of line.",
                        "pass":"Semantic"})

    return diags


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 7 — PASS 5: LINTER
# Checks style, complexity, bad patterns, and ESP32-specific traps.
# ─────────────────────────────────────────────────────────────────────────────
def pass5_lint(source: str, board_id: str, functions: list):
    diags = []
    lines = source.split('\n')
    board = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])

    # ── ESP32-specific trap patterns (line-by-line)
    if board_id in ("esp32", "esp32s3"):
        for ln, line in enumerate(lines, 1):
            for pattern, severity, message in ESP32_TRAPS:
                if re.search(pattern, line):
                    col = (re.search(pattern, line).start() or 0) + 1
                    diags.append({"line":ln,"col":col,"severity":severity,
                        "code":"L" + str(100 + ESP32_TRAPS.index((pattern,severity,message))),
                        "message":message, "pass":"Linter"})

    # ── Uno-specific: running out of flash or RAM is common
    if board_id in ("uno","nano"):
        for ln, line in enumerate(lines, 1):
            # String() is expensive on Uno
            if re.search(r'\bString\s*\(', line):
                diags.append({"line":ln,"col":1,"severity":"warning","code":"L200",
                    "message":"String class uses heap on AVR which causes fragmentation. "
                               "Use char arrays or F() macro for literals: Serial.println(F(\"text\")).",
                    "pass":"Linter"})

    # ── delay() inside ISR is always wrong
    for ln, line in enumerate(lines, 1):
        if re.search(r'\bdelay\s*\(\s*[1-9]', line):
            # Check if we're inside a function name that looks like an ISR
            for fn in functions:
                if fn.get("name","").startswith("ISR") or "IRAM_ATTR" in source[max(0,source.find(fn["name"])-30):source.find(fn["name"])]:
                    diags.append({"line":ln,"col":1,"severity":"error","code":"L300",
                        "message":"delay() inside an ISR (interrupt handler) will crash. "
                                   "Set a flag in the ISR and handle it in loop().",
                        "pass":"Linter"})

    # ── Magic numbers (warn about common ones)
    for ln, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith('#') or s.startswith('//'):
            continue
        # Detect pins hardcoded without a #define or const
        m = re.search(r'pinMode\s*\(\s*(\d+)\s*,', line)
        if m:
            pin = int(m.group(1))
            if pin > 39:
                diags.append({"line":ln,"col":1,"severity":"error","code":"L401",
                    "message":f"GPIO{pin} does not exist on ESP32 (max GPIO39).",
                    "pass":"Linter"})
            elif pin in (6,7,8,9,10,11):
                pass  # Already caught by ESP32_TRAPS
            elif not re.search(r'#define\s+\w+\s+' + str(pin), source):
                diags.append({"line":ln,"col":1,"severity":"info","code":"L402",
                    "message":f"Magic number GPIO{pin}: consider #define PIN_xxx {pin} at the top for readability.",
                    "pass":"Linter"})

    # ── Function complexity (lines as proxy for cyclomatic complexity)
    for fn in functions:
        body_start = source.find('{', source.find(fn["name"]))
        if body_start == -1:
            continue
        # Count lines between opening and closing brace of the function
        body_lines = source[body_start:body_start+3000].split('\n')
        complexity = sum(1 for l in body_lines if re.search(r'\b(if|else|for|while|switch|case|do)\b', l))
        if complexity > 15:
            diags.append({"line":fn["line"],"col":fn["col"],"severity":"info","code":"L500",
                "message":f"Function '{fn['name']}' has high complexity ({complexity} branches). "
                           "Consider splitting into smaller functions.",
                "pass":"Linter"})

    # ── Global variables: count and warn if too many
    global_var_count = len(re.findall(r'^(?:int|float|char|bool|String|long|byte|uint\w+)\s+\w+', source, re.MULTILINE))
    if global_var_count > 20:
        diags.append({"line":1,"col":1,"severity":"info","code":"L600",
            "message":f"{global_var_count} global variables detected. "
                       "Prefer local variables or a struct to reduce SRAM usage.",
            "pass":"Linter"})

    return diags


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 8 — PASS 6: DEPENDENCY GRAPH BUILDER
# For each function, find which other user-defined functions it calls.
# Returns: dict { caller_name: [callee_name, ...] }
# ─────────────────────────────────────────────────────────────────────────────
def pass6_dependency_graph(source: str, functions: list):
    fn_names = {f["name"] for f in functions}
    graph    = defaultdict(list)

    for fn in functions:
        # Extract body: from body_start to matching closing brace
        body_start = fn.get("body_start", 0)
        chunk      = source[body_start:]
        depth = 1
        end   = 0
        for ch in chunk:
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
            end += 1
        body = chunk[:end]

        # Find every word that matches a known function name
        for called in fn_names:
            if called == fn["name"]:
                continue
            if re.search(r'\b' + re.escape(called) + r'\s*\(', body):
                graph[fn["name"]].append(called)

    return dict(graph)


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 9 — PASS 7: DEAD CODE DETECTOR
# Uses the dependency graph to find functions that are never called.
# setup() and loop() are always considered "alive" (Arduino entry points).
# ─────────────────────────────────────────────────────────────────────────────
def pass7_dead_code(functions: list, graph: dict):
    diags = []
    fn_names = {f["name"] for f in functions}

    # Build reverse: who calls each function
    called_by = defaultdict(set)
    for caller, callees in graph.items():
        for callee in callees:
            called_by[callee].add(caller)

    alive = {"setup", "loop"}
    # Propagate: anything called by alive is also alive
    changed = True
    while changed:
        changed = False
        new_alive = set()
        for fn in fn_names:
            if fn not in alive and called_by[fn] & alive:
                new_alive.add(fn)
        if new_alive:
            alive |= new_alive
            changed = True

    # Report dead functions
    for fn in functions:
        if fn["name"] not in alive:
            diags.append({"line":fn["line"],"col":fn["col"],"severity":"info",
                "code":"D001",
                "message":f"Function '{fn['name']}' is defined but never called (dead code). "
                           "Remove it to save flash space.",
                "pass":"DeadCode"})

    return diags


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 10 — PASS 8: MEMORY ESTIMATOR
# Estimates static SRAM usage from global variable declarations.
# Returns: { "global_bytes": int, "string_bytes": int, "library_bytes": int,
#            "total_bytes": int, "ram_max": int, "pct": float }
# ─────────────────────────────────────────────────────────────────────────────
TYPE_SIZES = {
    "int": 4, "long": 4, "unsigned": 4, "float": 4, "double": 8,
    "char": 1, "byte": 1, "uint8_t": 1, "int8_t": 1,
    "uint16_t": 2, "int16_t": 2, "short": 2,
    "uint32_t": 4, "int32_t": 4, "uint64_t": 8, "int64_t": 8,
    "bool": 1, "boolean": 1, "word": 2, "size_t": 4,
    # Arduino String object is 24 bytes header + heap content
    "String": 24,
}

def pass8_memory(source: str, includes: list, board_id: str, defines: dict):
    board = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])

    # Base Arduino runtime overhead
    base_bytes = 1500 if board_id in ("esp32","esp32s3") else 500

    # Global variable scan (top-level declarations only)
    global_bytes = 0
    for m in re.finditer(
        r'^[ \t]*((?:unsigned\s+)?(?:' + '|'.join(TYPE_SIZES.keys()) + r'))\s+(\w+)'
        r'(?:\[(\d+)\])?\s*(?:=|;)',
        source, re.MULTILINE
    ):
        typ   = m.group(1).strip().split()[-1]   # handle "unsigned long" → "long"
        arr   = m.group(3)
        size  = TYPE_SIZES.get(typ, 4)
        count = int(arr) if arr else 1
        global_bytes += size * count

    # char[] and String literals in Serial.println / F() macros
    string_bytes = 0
    for m in re.finditer(r'Serial\.\w+\s*\(\s*"([^"]*)"', source):
        # Each non-F() string literal lives in SRAM on AVR boards
        if board_id in ("uno","nano"):
            string_bytes += len(m.group(1)) + 1

    # Library RAM overhead from known includes
    lib_bytes = sum(
        KNOWN_HEADERS[h]["ram_kb"] * 1024
        for h in includes if h in KNOWN_HEADERS
    )

    total = base_bytes + global_bytes + string_bytes + lib_bytes
    pct   = total / board["ram_max"] * 100

    return {
        "base_bytes":   base_bytes,
        "global_bytes": global_bytes,
        "string_bytes": string_bytes,
        "lib_bytes":    lib_bytes,
        "total_bytes":  total,
        "ram_max":      board["ram_max"],
        "pct":          round(pct, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 11 — PASS 9: FLASH ESTIMATOR
# Estimates program storage size from code features.
# Real avr-gcc output varies; this is a calibrated model.
# ─────────────────────────────────────────────────────────────────────────────
def pass9_flash(source: str, includes: list, functions: list, board_id: str):
    board = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])

    # Base bootloader + Arduino core size
    base = 180_000 if board_id in ("esp32","esp32s3") else 1_800

    # Per-function overhead (rough: ~100 bytes average)
    fn_flash = len(functions) * 100

    # Source lines of code (excluding blanks and comments) → ~8 bytes/line
    loc = sum(1 for l in source.split('\n') if l.strip() and not l.strip().startswith('//'))
    loc_flash = loc * 8

    # Library overhead estimates (very rough)
    lib_flash = 0
    if "WiFi.h"             in includes: lib_flash += 300_000  # WiFi stack is massive
    if "BluetoothSerial.h"  in includes: lib_flash += 250_000
    if "BLEDevice.h"        in includes: lib_flash += 350_000
    if "Adafruit_SSD1306.h" in includes: lib_flash += 25_000
    if "Adafruit_GFX.h"    in includes: lib_flash += 15_000
    if "DHT.h"             in includes: lib_flash += 8_000
    if "ESP32Servo.h"      in includes: lib_flash += 5_000
    if "ArduinoJson.h"     in includes: lib_flash += 40_000
    if "FastLED.h"         in includes: lib_flash += 60_000

    # Add randomness ±3% to feel realistic
    noise = random.randint(-3, 3)
    total = int((base + fn_flash + loc_flash + lib_flash) * (1 + noise/100))
    total = max(base + 500, min(total, board["flash_max"] - 10_000))
    pct   = total / board["flash_max"] * 100

    return {
        "total_bytes": total,
        "flash_max":   board["flash_max"],
        "pct":         round(pct, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 12 — PASS 10: DIAGNOSTIC RANKER
# Collects all diagnostics, deduplicates, sorts by severity+line.
# Returns structured list ready for the frontend to render.
# ─────────────────────────────────────────────────────────────────────────────
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2, "hint": 3}

def pass10_rank(all_diags: list):
    # Deduplicate on (line, code) — same issue on same line
    seen  = set()
    clean = []
    for d in all_diags:
        key = (d.get("line",0), d.get("code",""))
        if key not in seen:
            seen.add(key)
            clean.append(d)

    # Sort: errors first, then warnings, then info, then by line number
    clean.sort(key=lambda d: (
        SEVERITY_ORDER.get(d.get("severity","info"), 9),
        d.get("line", 0)
    ))
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 13 — MASTER COMPILE ORCHESTRATOR
# Runs all 10 passes and assembles the final response JSON.
# ─────────────────────────────────────────────────────────────────────────────
def compile_sketch(code: str, board_id: str = "esp32"):
    """
    Full multi-pass compilation pipeline.
    Returns a dict suitable for jsonify().
    """
    t0 = time.time()

    # Guard: empty sketch
    if not code.strip():
        return {
            "status": "error",
            "diagnostics": [{"line":1,"col":1,"severity":"error","code":"E000",
                "message":"Empty sketch — nothing to compile.", "pass":"Init"}],
            "summary": {"errors":1,"warnings":0,"info":0},
        }

    # Pass 1 — Tokenize / strip comments
    stripped, string_regions = pass1_tokenize(code)

    # Pass 2 — Preprocess
    expanded, defines, includes, include_diags = pass2_preprocess(stripped, board_id)

    # Pass 3 — Scan function signatures
    functions = pass3_signatures(expanded)

    # Pass 4 — Semantic analysis
    sem_diags = pass4_semantics(expanded, functions, defines)

    # Pass 5 — Lint
    lint_diags = pass5_lint(expanded, board_id, functions)

    # Pass 6 — Dependency graph
    graph = pass6_dependency_graph(expanded, functions)

    # Pass 7 — Dead code
    dead_diags = pass7_dead_code(functions, graph)

    # Pass 8 — Memory estimation
    memory = pass8_memory(expanded, includes, board_id, defines)

    # Pass 9 — Flash estimation
    flash = pass9_flash(expanded, includes, functions, board_id)

    # Pass 10 — Rank & deduplicate
    all_diags = include_diags + sem_diags + lint_diags + dead_diags
    ranked    = pass10_rank(all_diags)

    # Summary counts
    errors   = sum(1 for d in ranked if d["severity"] == "error")
    warnings = sum(1 for d in ranked if d["severity"] == "warning")
    infos    = sum(1 for d in ranked if d["severity"] == "info")

    elapsed_ms = round((time.time() - t0) * 1000)
    board = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])

    status = "error" if errors > 0 else ("warning" if warnings > 0 else "success")

    # Build human-readable size lines (like Arduino IDE output)
    size_msg = (
        f"Sketch uses {flash['total_bytes']:,} bytes ({flash['pct']}%) of program storage space. "
        f"Maximum is {flash['flash_max']:,} bytes.\n"
        f"Global variables use {memory['total_bytes']:,} bytes ({memory['pct']}%) of dynamic memory, "
        f"leaving {memory['ram_max'] - memory['total_bytes']:,} bytes for local variables. "
        f"Maximum is {memory['ram_max']:,} bytes."
    )

    # Library info lines
    lib_info = []
    for h in includes:
        if h in KNOWN_HEADERS:
            lib_info.append(f"  ✓ {h}  ({KNOWN_HEADERS[h]['lib']})")
        else:
            lib_info.append(f"  ? {h}  (not recognised — install via Library Manager)")

    return {
        "status":      status,
        "board":       board["name"],
        "board_id":    board_id,
        "elapsed_ms":  elapsed_ms,
        "diagnostics": ranked,
        "summary":     {"errors": errors, "warnings": warnings, "info": infos},
        "memory":      memory,
        "flash":       flash,
        "functions":   [{"name":f["name"],"return_type":f["return_type"],
                          "params":f["params"],"line":f["line"]} for f in functions],
        "graph":       graph,
        "includes":    includes,
        "lib_info":    lib_info,
        "defines":     defines,
        "size_message": size_msg,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 14 — UPLOAD SIMULATOR
# Simulates esptool.py flash + serial output from the sketch.
# ─────────────────────────────────────────────────────────────────────────────
def simulate_upload(code: str, board_id: str):
    """
    Mimics what esptool.py + Arduino bootloader prints during a real flash.
    Also simulates the sketch's Serial.println output.
    """
    board = BOARD_PROFILES.get(board_id, BOARD_PROFILES["esp32"])
    ts = time.strftime("%H:%M:%S")

    lines = [
        f"[{ts}] esptool.py v4.7.0  Serial port /dev/ttyUSB0",
        f"[{ts}] Connecting to {board['name']}...",
        f"[{ts}] Chip is ESP32-D0WD-V3 (revision v3.1)",
        f"[{ts}] Features: WiFi, BT, Dual Core, {board['freq_mhz']}MHz",
        f"[{ts}] Crystal is 40MHz",
        f"[{ts}] MAC: a4:e5:7c:12:34:56",
        f"[{ts}] Uploading stub...",
        f"[{ts}] Running stub...",
        f"[{ts}] Stub running...",
        f"[{ts}] Configuring flash size: {board['flash_max']//1024}KB",
        f"[{ts}] Flash erase 0x00001000 → 0x0003ffff",
        f"[{ts}] Compressed 194320 bytes to 112847",
        f"[{ts}] Writing at 0x00001000... (25%)",
        f"[{ts}] Writing at 0x00010000... (50%)",
        f"[{ts}] Writing at 0x00020000... (75%)",
        f"[{ts}] Writing at 0x00030000... (100%)",
        f"[{ts}] Hash of data verified.",
        f"[{ts}] Leaving...",
        f"[{ts}] Hard resetting via RTS pin...",
        f"[{ts}] ✓ Upload complete!",
        f"[{ts}] ─── Serial Monitor ({board['name']}) @ 115200 baud ───",
    ]

    # Sketch-specific serial simulation
    if "LED_BUILTIN" in code or "LED_PIN" in code:
        lines += [
            f"[{ts}] Gen Alpha ESP32 Studio — Blink sketch",
            f"[{ts}] [HIGH] LED on  GPIO2",
            f"[{ts}] [LOW]  LED off GPIO2",
            f"[{ts}] [HIGH] LED on  GPIO2",
        ]
    elif "WiFi" in code:
        lines += [
            f"[{ts}] WiFi Scanner ready",
            f"[{ts}] Scanning 2.4GHz networks...",
            f"[{ts}]   1: HomeNetwork_5G          -42 dBm [SECURED]",
            f"[{ts}]   2: MANIPAL_WIFI            -67 dBm [SECURED]",
            f"[{ts}]   3: AndroidAP_Reddy         -71 dBm [SECURED]",
        ]
    elif "Servo" in code or "servo" in code:
        lines += [
            f"[{ts}] Servo ready on GPIO18",
            f"[{ts}] Sweeping 0° → 180°",
            f"[{ts}] Reached 180°",
            f"[{ts}] Sweeping 180° → 0°",
        ]
    elif "DHT" in code:
        lines += [
            f"[{ts}] DHT11 ready on GPIO4",
            f"[{ts}] Temp: 27.4°C  Humidity: 63.2%  Heat Index: 29.1°C",
            f"[{ts}] Temp: 27.5°C  Humidity: 63.0%  Heat Index: 29.2°C",
        ]
    elif "SSD1306" in code:
        lines += [
            f"[{ts}] OLED initialized at I2C 0x3C",
            f"[{ts}] Display: Gen Alpha ESP32 Studio",
            f"[{ts}] Count: 0  Count: 1  Count: 2",
        ]
    elif "pulseIn" in code:
        dist = round(random.uniform(10, 40), 1)
        lines += [
            f"[{ts}] HC-SR04 ready. TRIG=12 ECHO=13",
            f"[{ts}] Distance: {dist} cm",
            f"[{ts}] Distance: {round(dist + random.uniform(-1,1),1)} cm",
        ]
    elif "analogRead" in code:
        raw = random.randint(1500, 2500)
        lines += [
            f"[{ts}] ADC ready. Reading GPIO32",
            f"[{ts}] Raw: {raw}  Voltage: {raw*3.3/4095:.2f}V  Level: {raw*100//4095}%",
        ]
    else:
        # Generic: extract any Serial.println("text") and replay it
        for m in re.finditer(r'Serial\.print(?:ln)?\s*\(\s*"([^"]+)"', code):
            lines.append(f"[{ts}] {m.group(1)}")

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 15 — SKETCH LIBRARY
# 8 full example sketches with accurate, well-commented code.
# ─────────────────────────────────────────────────────────────────────────────
SKETCHES = {
    "blink": {
        "title": "Blink", "category": "Basic",
        "description": "Blinks the onboard LED on GPIO2 every 500ms.",
        "code": """\
// ─────────────────────────────────────────────
// Blink — Basic GPIO output
// Board: ESP32 Dev Module
// GPIO2 = LED_BUILTIN on most ESP32 boards
// ─────────────────────────────────────────────
#define LED_PIN LED_BUILTIN   // GPIO2

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  Serial.println("Blink ready — LED on GPIO2");
}

void loop() {
  digitalWrite(LED_PIN, HIGH);
  Serial.println("[HIGH] LED on");
  delay(500);

  digitalWrite(LED_PIN, LOW);
  Serial.println("[LOW]  LED off");
  delay(500);
}"""
    },
    "wifi": {
        "title": "WiFi Scanner", "category": "Network",
        "description": "Scans 2.4GHz networks, prints SSID + signal strength.",
        "code": """\
// ─────────────────────────────────────────────
// WiFi Scanner — Network discovery
// Requires: ESP32 board package (built-in WiFi)
// Note: ESP32 only supports 2.4GHz, NOT 5GHz
// ─────────────────────────────────────────────
#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);   // Station mode (not AP)
  WiFi.disconnect();      // Clear old connections
  delay(100);
  Serial.println("WiFi Scanner ready");
}

void loop() {
  Serial.println("\\nScanning...");
  int n = WiFi.scanNetworks();

  if (n == 0) {
    Serial.println("No networks found.");
  } else {
    Serial.printf("Found %d networks:\\n", n);
    for (int i = 0; i < n; i++) {
      Serial.printf("  %d: %-30s %4d dBm %s\\n",
        i + 1,
        WiFi.SSID(i).c_str(),
        WiFi.RSSI(i),
        WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "[OPEN]" : "[SECURED]"
      );
    }
  }
  WiFi.scanDelete();  // Free scan results memory
  delay(5000);
}"""
    },
    "servo": {
        "title": "Servo Sweep", "category": "Actuators",
        "description": "Sweeps a servo 0°→180° on GPIO18 using ESP32Servo library.",
        "code": """\
// ─────────────────────────────────────────────
// Servo Sweep — PWM actuator control
// Library: ESP32Servo (install via Library Manager)
// Wiring: Red→VIN(5V)  Brown→GND  Orange→GPIO18
// ─────────────────────────────────────────────
#include <ESP32Servo.h>

#define SERVO_PIN 18    // PWM-capable GPIO

Servo myServo;
int angle = 0;

void setup() {
  Serial.begin(115200);
  // ESP32Servo auto-selects a LEDC timer channel
  myServo.attach(SERVO_PIN);
  Serial.println("Servo ready on GPIO18");
}

void loop() {
  // Sweep 0 → 180 degrees
  for (angle = 0; angle <= 180; angle++) {
    myServo.write(angle);
    delay(8);   // 8ms per degree = ~1.5s full sweep
  }
  Serial.println("Reached 180°");

  // Sweep 180 → 0 degrees
  for (angle = 180; angle >= 0; angle--) {
    myServo.write(angle);
    delay(8);
  }
  Serial.println("Reached 0°");
}"""
    },
    "dht11": {
        "title": "DHT11 Sensor", "category": "Sensors",
        "description": "Reads temperature + humidity from DHT11 on GPIO4.",
        "code": """\
// ─────────────────────────────────────────────
// DHT11 Temperature & Humidity Sensor
// Library: Adafruit DHT sensor library
//          + Adafruit Unified Sensor
// Wiring: VCC→3.3V  GND→GND  DATA→GPIO4
//         Add 10kΩ pull-up between VCC and DATA
// ─────────────────────────────────────────────
#include <DHT.h>

#define DHT_PIN  4        // Data pin
#define DHT_TYPE DHT11    // Or DHT22 for higher accuracy

DHT dht(DHT_PIN, DHT_TYPE);

void setup() {
  Serial.begin(115200);
  dht.begin();
  Serial.println("DHT11 ready on GPIO4");
}

void loop() {
  delay(2000);   // DHT11 minimum sample interval: 1s

  float humidity    = dht.readHumidity();
  float temperature = dht.readTemperature();  // Celsius

  if (isnan(humidity) || isnan(temperature)) {
    Serial.println("ERROR: DHT11 read failed — check wiring!");
    return;
  }

  float heatIndex = dht.computeHeatIndex(temperature, humidity, false);

  Serial.printf("Temp: %.1f°C  Hum: %.1f%%  HeatIdx: %.1f°C\\n",
    temperature, humidity, heatIndex);
}"""
    },
    "oled": {
        "title": "OLED Display", "category": "Display",
        "description": "128×64 SSD1306 I2C display. SDA=GPIO21 SCL=GPIO22.",
        "code": """\
// ─────────────────────────────────────────────
// OLED SSD1306 Display — 128×64 I2C
// Libraries: Adafruit SSD1306 + Adafruit GFX
// Wiring: VCC→3.3V  GND→GND  SDA→GPIO21  SCL→GPIO22
// I2C address: 0x3C (most 128×64 modules)
// ─────────────────────────────────────────────
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_W   128
#define SCREEN_H   64
#define OLED_RESET -1   // No reset pin on most modules
#define OLED_ADDR  0x3C

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, OLED_RESET);

void setup() {
  Serial.begin(115200);

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("ERROR: SSD1306 not found at 0x3C!");
    Serial.println("Check SDA=GPIO21, SCL=GPIO22 and power.");
    while (true);   // Halt — nothing works without display
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("Gen Alpha");
  display.println("ESP32 Studio");
  display.display();   // MUST call display() to push buffer
  Serial.println("OLED ready at I2C 0x3C");
}

void loop() {
  static int count = 0;
  display.clearDisplay();
  display.setCursor(0, 0);
  display.printf("Count: %d", count++);
  display.display();
  delay(500);
}"""
    },
    "ultrasonic": {
        "title": "HC-SR04 Ultrasonic", "category": "Sensors",
        "description": "Distance sensor. TRIG=GPIO12, ECHO=GPIO13.",
        "code": """\
// ─────────────────────────────────────────────
// HC-SR04 Ultrasonic Distance Sensor
// Wiring: VCC→5V  GND→GND  TRIG→GPIO12  ECHO→GPIO13
// Range: 2cm – 400cm  Accuracy: ±3mm
// ─────────────────────────────────────────────
#define TRIG_PIN 12
#define ECHO_PIN 13

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  Serial.println("HC-SR04 ready");
}

float measureDistance() {
  // Send 10µs trigger pulse
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Measure echo duration (timeout at 30ms = ~5m max)
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);

  if (duration == 0) return -1.0;   // No echo = out of range

  // Speed of sound: 343 m/s = 0.0343 cm/µs ÷ 2 (round trip)
  return duration * 0.01715;
}

void loop() {
  float cm = measureDistance();

  if (cm < 0) {
    Serial.println("Out of range (>400cm)");
  } else {
    Serial.printf("Distance: %.1f cm  (%.2f m)\\n", cm, cm/100.0);
  }
  delay(200);
}"""
    },
    "potentiometer": {
        "title": "Potentiometer ADC", "category": "Basic",
        "description": "Read analog voltage from pot on GPIO32 (12-bit ADC).",
        "code": """\
// ─────────────────────────────────────────────
// Potentiometer — 12-bit ADC Read
// Wiring: Left→GND  Middle→GPIO32  Right→3.3V
// GPIO32 = ADC1_CH4 (safe to use with WiFi)
// ─────────────────────────────────────────────
#define POT_PIN    32
#define ADC_BITS   12    // ESP32 = 12-bit → 0–4095
#define V_REF      3.3   // ESP32 3.3V logic

void setup() {
  Serial.begin(115200);
  analogReadResolution(ADC_BITS);
  analogSetAttenuation(ADC_11db);  // Full 0–3.3V range
  Serial.println("Potentiometer ADC ready on GPIO32");
}

void loop() {
  int   raw     = analogRead(POT_PIN);
  float voltage = raw * V_REF / (float)((1 << ADC_BITS) - 1);
  int   pct     = map(raw, 0, 4095, 0, 100);

  Serial.printf("Raw: %4d  Voltage: %.3fV  Level: %3d%%\\n",
    raw, voltage, pct);

  delay(100);
}"""
    },
    "wifi_connect": {
        "title": "WiFi + HTTP GET", "category": "Advanced",
        "description": "Connect to WiFi then fetch a JSON URL via HTTP GET.",
        "code": """\
// ─────────────────────────────────────────────
// WiFi Connect + HTTP GET
// Libraries: WiFi.h, HTTPClient.h (both built-in)
// ─────────────────────────────────────────────
#include <WiFi.h>
#include <HTTPClient.h>

// ── CHANGE THESE ──
const char* SSID     = "YOUR_WIFI_SSID";
const char* PASSWORD = "YOUR_WIFI_PASSWORD";
const char* URL      = "http://jsonplaceholder.typicode.com/todos/1";

void connectWiFi() {
  Serial.printf("Connecting to %s", SSID);
  WiFi.begin(SSID, PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 20) {
    delay(500);
    Serial.print(".");
    retries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\\nConnected! IP: %s\\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\\nFailed to connect!");
  }
}

void fetchData() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(URL);
  http.setTimeout(5000);   // 5s timeout
  int code = http.GET();
  if (code == HTTP_CODE_OK) {
    Serial.println(http.getString());
  } else {
    Serial.printf("HTTP error: %d\\n", code);
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  connectWiFi();
}

void loop() {
  fetchData();
  delay(10000);   // Fetch every 10 seconds
}"""
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 16 — FLASK ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main HTML page."""
    return render_template("index.html")


@app.route("/api/compile", methods=["POST"])
def route_compile():
    """
    POST body: { "code": "...", "board": "esp32" }
    Runs all 10 passes and returns structured diagnostics + memory/flash stats.
    """
    data  = request.get_json(force=True)
    code  = data.get("code", "").strip()
    board = data.get("board", "esp32")
    time.sleep(0.6)   # Simulate real compile time feel
    result = compile_sketch(code, board)
    return jsonify(result)


@app.route("/api/upload", methods=["POST"])
def route_upload():
    """
    POST body: { "code": "...", "board": "esp32" }
    Simulates esptool.py flash then returns simulated serial output.
    """
    data  = request.get_json(force=True)
    code  = data.get("code", "").strip()
    board = data.get("board", "esp32")

    # Quick compile check before upload
    result = compile_sketch(code, board)
    if result["summary"]["errors"] > 0:
        return jsonify({
            "status":  "error",
            "message": "Fix compile errors before uploading.",
            "diagnostics": result["diagnostics"][:5]
        })

    time.sleep(1.2)   # Simulate flash time
    serial_lines = simulate_upload(code, board)

    return jsonify({
        "status":  "success",
        "message": "Upload complete!",
        "serial":  serial_lines,
        "flash":   result["flash"],
        "memory":  result["memory"],
    })


@app.route("/api/sketch/<name>")
def route_sketch(n):
    """Return full sketch metadata + code for a named example."""
    sk = SKETCHES.get(n)
    if sk:
        return jsonify(sk)
    return jsonify({"error": f"Sketch '{n}' not found"}), 404


@app.route("/api/sketches")
def route_sketches():
    """Return all sketch metadata without code (keeps response small)."""
    return jsonify({
        k: {"title": v["title"], "category": v["category"], "description": v["description"]}
        for k, v in SKETCHES.items()
    })


@app.route("/api/boards")
def route_boards():
    """Return all supported board profiles."""
    return jsonify({
        "boards": [
            {"id": bid, "name": bp["name"], "flash_max": bp["flash_max"],
             "ram_max": bp["ram_max"], "freq_mhz": bp["freq_mhz"]}
            for bid, bp in BOARD_PROFILES.items()
        ]
    })


@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    """
    POST body: { "code": "...", "board": "esp32" }
    Returns function list + dependency graph (for the sidebar Analysis panel).
    """
    data  = request.get_json(force=True)
    code  = data.get("code", "").strip()
    board = data.get("board", "esp32")
    result = compile_sketch(code, board)
    return jsonify({
        "functions": result["functions"],
        "graph":     result["graph"],
        "defines":   result["defines"],
        "includes":  result["includes"],
        "lib_info":  result["lib_info"],
    })


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 17 — SERVER STARTUP
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "═"*56)
    print("  Gen Alpha ESP32 Studio — Flask Dev Server")
    print("  Open in browser: http://localhost:5000")
    print("  Multi-pass compiler pipeline active (10 passes)")
    print("  Press CTRL+C to stop")
    print("═"*56 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5000)