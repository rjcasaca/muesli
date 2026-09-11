import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// The flyout under the bar icon. Everything here talks to the muesli daemon
// through the CLI, so the panel has no state the daemon doesn't already own:
// status comes from `muesli status --json`, the meeting list from
// `muesli list --json`, the live transcript is transcript.md itself, and the
// settings view reads/writes config.toml through `muesli config`.
Panel {
  id: root
  moduleName: "rcasaca.muesli"
  ipcTarget: "rcasaca.muesli"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  // "main" | "settings"
  property string view: "main"

  // --- state, refreshed while open ------------------------------------------
  property var st: ({})
  property var sessions: []
  property var transcriptTail: []
  property var cfg: ({})
  property var tpl: ({ templates: [], tones: [] })
  property var keys: ({ keys: [] })
  property var usage: ({ stt: { rows: [] }, enhance: { rows: [] } })
  property string usagePeriod: "month"
  property string actionStatus: ""
  property string lastError: ""

  readonly property bool daemonUp: st.recording !== undefined
  readonly property bool recording: st.recording === true
  readonly property string meeting: st.meeting ? String(st.meeting) : ""
  readonly property string sessionDir: st.session_dir ? String(st.session_dir) : ""
  readonly property int transcriptLines: Number(root.setting("transcriptLines", 8))

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property string heroIcon: recording ? "●" : (meeting !== "" ? "◉" : "○")
  readonly property string heroTitle: !daemonUp ? "muesli daemon is not running"
                                     : recording ? "Recording · " + String(st.title || "meeting")
                                     : meeting !== "" ? meeting + " is on the mic"
                                     : "Idle"
  readonly property string heroMeta: !daemonUp ? "systemctl --user start muesli"
                                    : recording ? String(st.elapsed_text || "") + " · " + Number(st.lines || 0) + " lines"
                                                  + (Number(st.pending || 0) > 0 ? " · " + st.pending + " chunk(s) in flight" : "")
                                                  + " · " + String(st.provider || "")
                                    : meeting !== "" ? "Flip the switch to start recording"
                                    : "Nothing on the microphone · " + String(st.provider || "")

  // config accessors (cfg mirrors `muesli config show --json`)
  function c(section, key, fallback) {
    var s = root.cfg[section]
    if (s === undefined) return fallback
    if (key === undefined) return s
    return s[key] === undefined ? fallback : s[key]
  }
  readonly property string sttProvider: String(c("stt", "provider", "openai"))
  readonly property string enhBackend: String(c("enhance", "backend", "claude"))
  readonly property var templateOptions: (root.tpl.templates || []).map(function(t) { return { value: t.name, label: t.title } })
  readonly property var toneOptions: (root.tpl.tones || []).map(function(t) { return { value: t.name, label: t.name } })
  readonly property string usageLine: {
    var u = root.usage
    if (!u || !u.stt) return ""
    var mins = Number(u.stt.seconds || 0) / 60
    var e = u.enhance || {}
    return mins.toFixed(1) + " min transcribed ≈ $" + Number(u.stt.cost_usd || 0).toFixed(2)
         + " · " + Number(e.runs || 0) + " enhance" + (Number(e.runs || 0) === 1 ? "" : "s")
         + " " + (e.estimated ? "~" : "") + "$" + Number(e.cost_usd || 0).toFixed(2)
  }

  // --- actions --------------------------------------------------------------
  function run(command, status) {
    if (root.bar) root.bar.run(command)
    root.actionStatus = status || ""
    root.lastError = ""
    statusClear.restart()
    afterAction.restart()
  }

  function toggleRecording() { run("muesli toggle", recording ? "Stopping…" : "Starting recording…") }
  function enhance(path) {
    run("muesli enhance --session " + Util.shellQuote(path || "latest"), "Enhancing — this runs your LLM and may take a minute")
  }
  function openFolder(path) { run("xdg-open " + Util.shellQuote(path), "") }
  function openTui() { run("omarchy-launch-or-focus-tui muesli tui", ""); root.close() }
  function openEditor(path) { run("omarchy-launch-editor " + Util.shellQuote(path), "") }
  function openNotesDir() { openFolder(String(c("notes_dir", undefined, Quickshell.env("HOME") + "/notes/meetings"))) }
  function editTemplate(name) {
    // `templates edit` copies a built-in into ~/.config/muesli/templates first and prints the path.
    run("sh -c " + Util.shellQuote("omarchy-launch-editor \"$(muesli templates edit " + Util.shellQuote(name) + ")\""),
        "Opening " + name + " in your editor")
  }
  function newTemplate(name) {
    var slug = String(name).trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    if (slug === "") return
    run("sh -c " + Util.shellQuote("omarchy-launch-editor \"$(muesli templates new " + Util.shellQuote(slug) + ")\""),
        "Created template " + slug)
  }

  // Serialised writes to config.toml; each one makes the daemon reload.
  property var setQueue: []
  function setConfig(key, value) {
    root.setQueue = root.setQueue.concat([[key, value]])
    if (!setProc.running) nextSet()
  }
  function nextSet() {
    if (root.setQueue.length === 0) { root.refreshConfig(); return }
    var item = root.setQueue[0]
    root.setQueue = root.setQueue.slice(1)
    setProc.command = ["muesli", "config", "set", "--json", String(item[0]), JSON.stringify(item[1])]
    setProc.running = true
  }
  function saveKey(name, value) {
    if (!value || String(value).trim() === "") return
    keyProc.command = ["sh", "-c", "muesli keys --set " + Util.shellQuote(name + "=" + String(value).trim())
                       + " && systemctl --user restart muesli"]
    keyProc.running = true
    root.actionStatus = "Saving " + name + " and restarting the daemon…"
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
    if (!listProc.running) listProc.running = true
    if (!usageProc.running) { usageProc.command = ["muesli", "usage", root.usagePeriod, "--json"]; usageProc.running = true }
    refreshTail()
  }
  function refreshConfig() {
    if (!cfgProc.running) cfgProc.running = true
    if (!tplProc.running) tplProc.running = true
    if (!keysProc.running) keysProc.running = true
  }
  function refreshTail() {
    if (root.sessionDir === "") { root.transcriptTail = []; return }
    if (tailProc.running) return
    // Only the utterance lines (**[mm:ss] Me:** …), not the markdown header.
    tailProc.command = ["sh", "-c", "grep '^\\*\\*\\[' \"$1\" 2>/dev/null | tail -n \"$2\"", "muesli-tail",
                        root.sessionDir + "/transcript.md", String(root.transcriptLines)]
    tailProc.running = true
  }

  function parseJson(text, fallback) { try { return JSON.parse(text || "") } catch (e) { return fallback } }

  Process { id: statusProc; command: ["muesli", "status", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: { root.st = root.parseJson(text, {}); root.refreshTail() } } }
  Process { id: listProc; command: ["muesli", "list", "--json", "-n", "6"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: { var l = root.parseJson(text, []); root.sessions = Array.isArray(l) ? l : [] } } }
  Process { id: usageProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.usage = root.parseJson(text, root.usage) } }
  Process { id: cfgProc; command: ["muesli", "config", "show", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.cfg = root.parseJson(text, {}) } }
  Process { id: tplProc; command: ["muesli", "templates", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.tpl = root.parseJson(text, root.tpl) } }
  Process { id: keysProc; command: ["muesli", "keys", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.keys = root.parseJson(text, root.keys) } }
  Process { id: setProc
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim() !== "") root.lastError = text.trim() }
    onExited: function(code) { if (code === 0) { root.actionStatus = "Saved"; statusClear.restart() } root.nextSet() } }
  Process { id: keyProc
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: if (text.trim() !== "") root.lastError = text.trim() }
    onExited: function(code) { root.actionStatus = code === 0 ? "Key saved, daemon restarted" : ""; statusClear.restart(); Qt.callLater(root.refreshConfig); afterAction.restart() } }
  Process { id: tailProc
    stdout: StdioCollector { waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").split("\n").filter(function(l) { return l.trim() !== "" })
        root.transcriptTail = lines.map(function(l) {
          // transcript.md lines look like:  **[12:34] Me:** text
          var m = l.match(/^\*\*\[([0-9:]+)\]\s+(Me|Them):\*\*\s*(.*)$/)
          return m ? { t: m[1], speaker: m[2], text: m[3] } : { t: "", speaker: "", text: l }
        })
      } } }

  Timer { id: afterAction; interval: 600; onTriggered: root.refresh() }
  Timer { id: statusClear; interval: 6000; onTriggered: root.actionStatus = "" }
  Timer { interval: 2000; running: root.opened; repeat: true; triggeredOnStart: true; onTriggered: root.refresh() }
  onOpenedChanged: { if (root.opened) root.refreshConfig(); else root.view = "main" }
  onViewChanged: if (root.view === "settings") root.refreshConfig()
  onUsagePeriodChanged: { usageProc.command = ["muesli", "usage", root.usagePeriod, "--json"]; usageProc.running = true }

  // --- ui -----------------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(Number(root.setting("panelWidth", 420))))
    contentHeight: panel.fittedContentHeight(column.implicitHeight,
                     Style.space(Number(root.setting("panelHeight", 640)) + (root.view === "settings" ? 160 : 0)))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.view === "settings" ? root.view = "main" : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (root.view !== "main") return
        if (t === "r" || t === "R" || t === " ") root.toggleRecording()
        else if (t === "e" || t === "E") root.enhance("latest")
        else if (t === "t" || t === "T") root.openTui()
        else if (t === "s" || t === "S" || t === ",") root.view = "settings"
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // ====================== hero (both views) ======================
          PanelHero {
            id: hero
            width: parent.width
            title: root.view === "settings" ? "muesli settings" : root.heroTitle
            meta: root.view === "settings" ? "Changes are written to ~/.config/muesli/config.toml" : root.heroMeta
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.view === "settings" || root.recording || root.meeting !== "" ? 1.0 : 0.5
            iconComponent: Component {
              Text {
                text: root.view === "settings" ? "󰒓" : root.heroIcon
                color: root.recording && root.view === "main" ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                SequentialAnimation on opacity {
                  running: root.view === "main" && root.meeting !== "" && !root.recording
                  loops: Animation.Infinite
                  NumberAnimation { from: 1.0; to: 0.35; duration: 750; easing.type: Easing.InOutQuad }
                  NumberAnimation { from: 0.35; to: 1.0; duration: 750; easing.type: Easing.InOutQuad }
                }
              }
            }
            trailingControl: Component {
              RowLayout {
                spacing: Style.space(6)
                ToggleSwitch {
                  id: recSwitch
                  visible: root.view === "main" && root.daemonUp
                  checked: root.recording
                  foreground: hero.foreground
                  accent: root.urgent
                  onToggled: root.toggleRecording()
                  PanelToolTip { visible: recSwitch.containsMouse; text: root.recording ? "Stop recording  (R)" : "Start recording  (R)"; fontFamily: hero.fontFamily }
                }
                PanelActionButton {
                  iconText: root.view === "settings" ? "󰅖" : "󰒓"
                  tooltipText: root.view === "settings" ? "Back  (Esc)" : "Settings  (S)"
                  foreground: hero.foreground
                  fontFamily: hero.fontFamily
                  onClicked: root.view = root.view === "settings" ? "main" : "settings"
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.actionStatus !== "" || root.lastError !== ""
            width: parent.width
            text: root.lastError !== "" ? root.lastError : root.actionStatus
            color: root.lastError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          // ====================== main view ======================
          Column {
            visible: root.view === "main"
            width: parent.width
            spacing: Style.space(12)

            PanelSeparator { visible: root.sessionDir !== ""; width: parent.width }

            Column {
              visible: root.sessionDir !== ""
              width: parent.width
              spacing: Style.space(6)
              PanelSectionHeader { width: parent.width; text: root.recording ? "Live transcript" : "Last transcript"; foreground: root.dim; fontFamily: root.fontFamily }
              Text {
                visible: root.transcriptTail.length === 0
                width: parent.width
                text: root.recording ? "Listening… the first chunk lands after the first 30 s." : "No transcript yet."
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap
              }
              Repeater {
                model: root.transcriptTail
                delegate: RowLayout {
                  width: column.width
                  spacing: Style.space(6)
                  Text { text: modelData.t; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; Layout.alignment: Qt.AlignTop }
                  Text { visible: modelData.speaker !== ""; text: modelData.speaker + ":"; color: modelData.speaker === "Me" ? Color.accent : root.urgent
                         font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; Layout.alignment: Qt.AlignTop }
                  Text { text: modelData.text; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
              }
            }

            PanelSeparator { width: parent.width }

            Column {
              width: parent.width
              spacing: Style.space(4)
              PanelSectionHeader { width: parent.width; text: "Recent meetings"; foreground: root.dim; fontFamily: root.fontFamily }
              Text { visible: root.sessions.length === 0; width: parent.width; text: "No meetings recorded yet."; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
              Repeater {
                model: root.sessions
                delegate: CursorSurface {
                  width: column.width
                  implicitHeight: Math.max(nameCol.implicitHeight + Style.space(10), Style.spacing.controlHeight)
                  foreground: root.foreground
                  RowLayout {
                    anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(4); spacing: Style.space(6)
                    Column {
                      id: nameCol
                      Layout.fillWidth: true; spacing: 0
                      Text { width: parent.width; text: String(modelData.name); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; elide: Text.ElideRight }
                      Text { text: Number(modelData.lines || 0) + " lines" + (modelData.enhanced ? " · enhanced" : ""); color: modelData.enhanced ? Color.accent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                    }
                    PanelActionButton { iconText: "󰁨"; tooltipText: modelData.enhanced ? "Re-run enhance" : "Enhance with your LLM"; foreground: root.foreground; fontFamily: root.fontFamily; Layout.alignment: Qt.AlignVCenter; onClicked: root.enhance(String(modelData.path)) }
                    PanelActionButton { iconText: "󰝰"; tooltipText: "Open folder"; foreground: root.foreground; fontFamily: root.fontFamily; Layout.alignment: Qt.AlignVCenter; onClicked: root.openFolder(String(modelData.path)) }
                  }
                }
              }
            }

            PanelSeparator { width: parent.width }

            // usage one-liner
            RowLayout {
              width: parent.width
              spacing: Style.space(6)
              Text { text: "This month"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              Text { Layout.fillWidth: true; text: root.usageLine; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption; elide: Text.ElideRight; horizontalAlignment: Text.AlignRight }
            }

            RowLayout {
              width: parent.width
              spacing: Style.space(8)
              Button { text: "Open TUI"; iconText: ""; tooltipText: "Full terminal UI: notes pane, /commands  (T)"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; Layout.fillWidth: true; onClicked: root.openTui() }
              Button { text: "Enhance latest"; iconText: "󰁨"; tooltipText: "muesli enhance --session latest  (E)"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; enabled: root.sessions.length > 0; Layout.fillWidth: true; onClicked: root.enhance("latest") }
              PanelActionButton { iconText: "󰝰"; tooltipText: "Open the notes folder"; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.openNotesDir() }
            }
          }

          // ====================== settings view ======================
          Column {
            visible: root.view === "settings"
            width: parent.width
            spacing: Style.space(12)

            // ---- transcription ----
            PanelSeparator { width: parent.width }
            Column {
              width: parent.width
              spacing: Style.space(8)
              PanelSectionHeader { width: parent.width; text: "Transcription"; foreground: root.dim; fontFamily: root.fontFamily }

              Dropdown {
                width: parent.width
                label: "Provider"
                foreground: root.foreground; fontFamily: root.fontFamily
                options: [ { value: "openai", label: "OpenAI-compatible (Groq, OpenAI, local server)" },
                           { value: "xai", label: "xAI Grok STT" },
                           { value: "local", label: "Local (faster-whisper / whisper.cpp)" } ]
                value: root.sttProvider
                onChanged: function(v) { root.setConfig("stt.provider", v) }
              }

              Column {
                visible: root.sttProvider === "openai"
                width: parent.width
                spacing: Style.space(6)
                SettingField { label: "Endpoint"; value: String(root.c("stt", "base_url", "")); placeholder: "https://api.groq.com/openai/v1"; onCommitted: function(v) { root.setConfig("stt.base_url", v) } }
                SettingField { label: "Model"; value: String(root.c("stt", "model", "")); placeholder: "whisper-large-v3-turbo"; onCommitted: function(v) { root.setConfig("stt.model", v) } }
                SettingField { label: "Key variable"; value: String(root.c("stt", "api_key_env", "")); placeholder: "GROQ_API_KEY"; onCommitted: function(v) { root.setConfig("stt.api_key_env", v) } }
              }
              Column {
                visible: root.sttProvider === "local"
                width: parent.width
                spacing: Style.space(6)
                Dropdown {
                  width: parent.width; label: "Engine"; foreground: root.foreground; fontFamily: root.fontFamily
                  options: [ { value: "faster-whisper", label: "faster-whisper (pip install 'muesli[local]')" }, { value: "whisper-cpp", label: "whisper.cpp (whisper-cli on PATH)" } ]
                  value: String(root.c("stt", "local_engine", "faster-whisper"))
                  onChanged: function(v) { root.setConfig("stt.local_engine", v) }
                }
                SettingField { label: "Model"; value: String(root.c("stt", "local_model", "")); placeholder: "tiny · base · small · medium · large-v3 · or a .bin path"; onCommitted: function(v) { root.setConfig("stt.local_model", v) } }
              }
              SettingField { label: "Language"; value: String(root.c("language", undefined, "")); placeholder: "auto-detect (or pt, en, …)"; onCommitted: function(v) { root.setConfig("language", v) } }
              SettingField { label: "Vocabulary"; value: (root.c("vocabulary", undefined, []) || []).join(", "); placeholder: "names, jargon — comma separated"
                             onCommitted: function(v) { root.setConfig("vocabulary", v.split(",").map(function(x) { return x.trim() }).filter(function(x) { return x !== "" })) } }

              // API keys: presence only, never the value
              Repeater {
                model: root.keys.keys || []
                delegate: Column {
                  width: parent.width
                  spacing: Style.space(4)
                  RowLayout {
                    width: parent.width
                    Text { text: String(modelData.name); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall; Layout.fillWidth: true }
                    Text { text: modelData.set ? "set  " + String(modelData.hint) : "missing"; color: modelData.set ? Color.accent : root.urgent; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                  }
                  RowLayout {
                    width: parent.width
                    spacing: Style.space(6)
                    TextField {
                      id: keyField
                      Layout.fillWidth: true
                      password: true
                      placeholderText: modelData.set ? "paste a new key to replace it" : "paste your API key"
                      foreground: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall
                      onAccepted: { root.saveKey(String(modelData.name), text); text = "" }
                    }
                    Button { text: "Save"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; enabled: keyField.text.trim() !== ""
                             onClicked: { root.saveKey(String(modelData.name), keyField.text); keyField.text = "" } }
                  }
                }
              }
            }

            // ---- AI / enhance ----
            PanelSeparator { width: parent.width }
            Column {
              width: parent.width
              spacing: Style.space(8)
              PanelSectionHeader { width: parent.width; text: "AI enhance"; foreground: root.dim; fontFamily: root.fontFamily }
              Dropdown {
                width: parent.width; label: "Backend"; foreground: root.foreground; fontFamily: root.fontFamily
                options: [ { value: "claude", label: "Claude Code  (claude -p · real token usage)" },
                           { value: "grok", label: "Grok CLI  (grok -p)" },
                           { value: "ollama", label: "Ollama  (local models)" },
                           { value: "custom", label: "Custom shell command" } ]
                value: root.enhBackend
                onChanged: function(v) { root.setConfig("enhance.backend", v) }
              }
              SettingField {
                visible: root.enhBackend !== "custom"
                label: "Model"; value: String(root.c("enhance", "model", ""))
                placeholder: root.enhBackend === "ollama" ? "llama3.1" : root.enhBackend === "claude" ? "backend default (e.g. claude-sonnet-5)" : "backend default"
                onCommitted: function(v) { root.setConfig("enhance.model", v) }
              }
              SettingField {
                visible: root.enhBackend === "custom"
                label: "Command"; value: String(root.c("enhance", "command", ""))
                placeholder: 'my-llm < "{prompt_file}" > "{output}"'
                onCommitted: function(v) { root.setConfig("enhance.command", v) }
              }
              Text {
                visible: root.enhBackend === "custom"
                width: parent.width
                text: "Placeholders: {prompt_file} {transcript} {notes} {output} {dir}"
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.WordWrap
              }
              Dropdown {
                width: parent.width; label: "Default template"; foreground: root.foreground; fontFamily: root.fontFamily
                options: root.templateOptions
                value: String(root.c("enhance", "default_template", "general"))
                onChanged: function(v) { root.setConfig("enhance.default_template", v) }
              }
              Dropdown {
                width: parent.width; label: "Default tone"; foreground: root.foreground; fontFamily: root.fontFamily
                options: root.toneOptions
                value: String(root.c("enhance", "default_tone", "concise"))
                onChanged: function(v) { root.setConfig("enhance.default_tone", v) }
              }
            }

            // ---- templates ----
            PanelSeparator { width: parent.width }
            Column {
              width: parent.width
              spacing: Style.space(4)
              RowLayout {
                width: parent.width
                PanelSectionHeader { Layout.fillWidth: true; text: "Templates"; foreground: root.dim; fontFamily: root.fontFamily }
                PanelActionButton { iconText: "󰝰"; tooltipText: "Open ~/.config/muesli/templates"; foreground: root.foreground; fontFamily: root.fontFamily
                                    onClicked: root.openFolder(String(root.tpl.user_dir || Quickshell.env("HOME") + "/.config/muesli/templates")) }
              }
              Repeater {
                model: root.tpl.templates || []
                delegate: CursorSurface {
                  width: column.width
                  implicitHeight: Math.max(tplCol.implicitHeight + Style.space(8), Style.spacing.controlHeight)
                  foreground: root.foreground
                  RowLayout {
                    anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(4); spacing: Style.space(6)
                    Column {
                      id: tplCol
                      Layout.fillWidth: true; spacing: 0
                      RowLayout {
                        spacing: Style.space(6)
                        Text { text: String(modelData.title); color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall }
                        Text { visible: modelData.user === true; text: "yours"; color: Color.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                        Text { visible: modelData.name === String(root.c("enhance", "default_template", "general")); text: "default"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                      }
                      Text { width: tplCol.width; text: String(modelData.description); color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; elide: Text.ElideRight; maximumLineCount: 1 }
                    }
                    PanelActionButton { iconText: "󰏫"; tooltipText: modelData.user ? "Edit" : "Customise (copies it to your templates folder)"; foreground: root.foreground; fontFamily: root.fontFamily; Layout.alignment: Qt.AlignVCenter; onClicked: root.editTemplate(String(modelData.name)) }
                  }
                }
              }
              RowLayout {
                width: parent.width
                spacing: Style.space(6)
                TextField {
                  id: newTplField
                  Layout.fillWidth: true
                  placeholderText: "new template name…"
                  foreground: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall
                  onAccepted: { root.newTemplate(text); text = "" }
                }
                Button { text: "Create"; iconText: "󰐕"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; enabled: newTplField.text.trim() !== ""
                         onClicked: { root.newTemplate(newTplField.text); newTplField.text = "" } }
              }
              Text { width: parent.width; text: "A template is the instruction; the transcript and your notes are appended. Markdown in, markdown out."
                     color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.WordWrap }
            }

            // ---- detection ----
            PanelSeparator { width: parent.width }
            Column {
              width: parent.width
              spacing: Style.space(2)
              PanelSectionHeader { width: parent.width; text: "Meeting detection"; foreground: root.dim; fontFamily: root.fontFamily }
              Toggle { width: parent.width; label: "Watch the microphone"; description: "Notice when Teams, Zoom, a browser… opens the mic"; checked: root.c("detect", "enabled", true) === true
                       foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.setConfig("detect.enabled", !checked) }
              Toggle { width: parent.width; label: "Auto-start recording"; description: "Record by itself when a meeting app opens the mic"; checked: root.c("detect", "auto_start", false) === true
                       foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.setConfig("detect.auto_start", !checked) }
              Toggle { width: parent.width; label: "Notify"; description: "Desktop notification when a meeting is detected"; checked: root.c("detect", "notify", true) === true
                       foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.setConfig("detect.notify", !checked) }
            }

            // ---- usage ----
            PanelSeparator { width: parent.width }
            Column {
              width: parent.width
              spacing: Style.space(6)
              RowLayout {
                width: parent.width
                PanelSectionHeader { Layout.fillWidth: true; text: "Consumption"; foreground: root.dim; fontFamily: root.fontFamily }
                Dropdown {
                  width: Style.space(120); showLabel: false; foreground: root.foreground; fontFamily: root.fontFamily
                  options: [ { value: "today", label: "Today" }, { value: "month", label: "This month" }, { value: "all", label: "All time" } ]
                  value: root.usagePeriod
                  onChanged: function(v) { root.usagePeriod = v }
                }
              }
              UsageRow { name: "Transcription"; detail: (Number(root.usage.stt.seconds || 0) / 60).toFixed(1) + " min · " + Number(root.usage.stt.requests || 0) + " requests"; amount: "$" + Number(root.usage.stt.cost_usd || 0).toFixed(3) }
              Repeater {
                model: root.usage.stt.rows || []
                delegate: UsageRow { indent: true; name: String(modelData.provider) + "/" + String(modelData.model); detail: (Number(modelData.seconds) / 60).toFixed(1) + " min" + (Number(modelData.errors) > 0 ? " · " + modelData.errors + " errors" : ""); amount: "$" + Number(modelData.cost_usd).toFixed(3) }
              }
              UsageRow { name: "Enhance"; detail: Number(root.usage.enhance.runs || 0) + " runs · " + Number(root.usage.enhance.input_tokens || 0).toLocaleString() + " in / " + Number(root.usage.enhance.output_tokens || 0).toLocaleString() + " out tokens"
                         amount: (root.usage.enhance.estimated ? "~" : "") + "$" + Number(root.usage.enhance.cost_usd || 0).toFixed(3) }
              Repeater {
                model: root.usage.enhance.rows || []
                delegate: UsageRow { indent: true; name: String(modelData.backend) + "/" + (String(modelData.model) || "default"); detail: Number(modelData.runs) + " runs · " + Number(modelData.input_tokens).toLocaleString() + "/" + Number(modelData.output_tokens).toLocaleString() + " tok"; amount: (modelData.estimated ? "~" : "") + "$" + Number(modelData.cost_usd).toFixed(3) }
              }
              Text { width: parent.width; text: "Estimates from the [costs] table in config.toml; \"~\" means tokens or price were estimated. Claude reports its own usage."
                     color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; wrapMode: Text.WordWrap }
            }

            PanelSeparator { width: parent.width }
            RowLayout {
              width: parent.width
              spacing: Style.space(8)
              Button { text: "Edit config.toml"; iconText: "󰏫"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; Layout.fillWidth: true
                       onClicked: root.openEditor(String(root.cfg.path || Quickshell.env("HOME") + "/.config/muesli/config.toml")) }
              Button { text: "Usage log"; iconText: "󰈙"; foreground: root.foreground; fontSize: Style.font.bodySmall; bordered: true; Layout.fillWidth: true
                       onClicked: root.openEditor(String(root.usage.log || Quickshell.env("HOME") + "/.local/state/muesli/usage.jsonl")) }
            }
          }
        }
      }
    }
  }

  // --- small reusable rows -----------------------------------------------------
  component SettingField: RowLayout {
    id: field
    property string label: ""
    property string value: ""
    property string placeholder: ""
    signal committed(string value)
    width: parent ? parent.width : 0
    spacing: Style.space(8)
    Text { text: field.label; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; Layout.preferredWidth: Style.space(88) }
    TextField {
      id: input
      Layout.fillWidth: true
      text: field.value
      placeholderText: field.placeholder
      foreground: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.bodySmall
      // Re-sync when the config reloads underneath us, but never while the user is typing.
      onActiveFocusChanged: if (!activeFocus && text !== field.value) field.committed(text)
      onAccepted: if (text !== field.value) field.committed(text)
      Connections { target: field; function onValueChanged() { if (!input.activeFocus) input.text = field.value } }
    }
  }

  component UsageRow: RowLayout {
    id: urow
    property bool indent: false
    property string name: ""
    property string detail: ""
    property string amount: ""
    width: parent ? parent.width : 0
    spacing: Style.space(6)
    Item { visible: urow.indent; Layout.preferredWidth: Style.space(12) }
    Text { text: urow.name; color: urow.indent ? root.dim : root.foreground; font.family: root.fontFamily; font.pixelSize: urow.indent ? Style.font.caption : Style.font.bodySmall; Layout.preferredWidth: Style.space(110); elide: Text.ElideRight }
    Text { Layout.fillWidth: true; text: urow.detail; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; elide: Text.ElideRight }
    Text { text: urow.amount; color: urow.indent ? root.dim : root.foreground; font.family: root.fontFamily; font.pixelSize: urow.indent ? Style.font.caption : Style.font.bodySmall }
  }
}
