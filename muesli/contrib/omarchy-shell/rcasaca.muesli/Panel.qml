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
// `muesli list --json`, and the live transcript is transcript.md itself,
// which the daemon rewrites after every chunk.
Panel {
  id: root
  moduleName: "rcasaca.muesli"
  ipcTarget: "rcasaca.muesli"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  // --- state, refreshed by the host widget's poll ---------------------------
  property var st: ({})
  property var sessions: []
  property var transcriptTail: []
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

  // --- actions ----------------------------------------------------------------
  function run(command, status) {
    if (root.bar) root.bar.run(command)
    root.actionStatus = status || ""
    root.lastError = ""
    statusClear.restart()
    afterAction.restart()
  }

  function toggleRecording() { run("muesli toggle", recording ? "Stopping…" : "Starting recording…") }
  function enhance(path) {
    run("muesli enhance --session " + Util.shellQuote(path || "latest"), "Enhancing — this runs your LLM command and may take a minute")
  }
  function openFolder(path) { run("xdg-open " + Util.shellQuote(path), "") }
  function openTui() { run("omarchy-launch-or-focus-tui muesli tui", ""); root.close() }
  function openNotesDir() {
    if (root.sessions.length > 0) {
      var p = String(root.sessions[0].path)
      openFolder(p.substring(0, p.lastIndexOf("/")))
    } else {
      openFolder(Quickshell.env("HOME") + "/notes/meetings")
    }
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
    if (!listProc.running) listProc.running = true
    refreshTail()
  }

  function refreshTail() {
    if (root.sessionDir === "") { root.transcriptTail = []; return }
    if (tailProc.running) return
    // Only the utterance lines (**[mm:ss] Me:** …), not the markdown header.
    tailProc.command = ["sh", "-c", "grep '^\\*\\*\\[' \"$1\" 2>/dev/null | tail -n \"$2\"", "muesli-tail",
                        root.sessionDir + "/transcript.md", String(root.transcriptLines)]
    tailProc.running = true
  }

  Process {
    id: statusProc
    command: ["muesli", "status", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.st = JSON.parse(text || "{}") } catch (e) { root.st = {} }
        root.refreshTail()
      }
    }
  }

  Process {
    id: listProc
    command: ["muesli", "list", "--json", "-n", "6"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { var l = JSON.parse(text || "[]"); root.sessions = Array.isArray(l) ? l : [] } catch (e) { root.sessions = [] }
      }
    }
  }

  Process {
    id: tailProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").split("\n").filter(function(l) { return l.trim() !== "" })
        root.transcriptTail = lines.map(function(l) {
          // transcript.md lines look like:  **[12:34] Me:** text
          var m = l.match(/^\*\*\[([0-9:]+)\]\s+(Me|Them):\*\*\s*(.*)$/)
          return m ? { t: m[1], speaker: m[2], text: m[3] } : { t: "", speaker: "", text: l }
        })
      }
    }
  }

  Timer { id: afterAction; interval: 500; onTriggered: root.refresh() }
  Timer { id: statusClear; interval: 6000; onTriggered: root.actionStatus = "" }
  Timer {
    interval: 2000
    running: root.opened
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // --- ui -----------------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(Number(root.setting("panelWidth", 400))))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(Number(root.setting("panelHeight", 620))))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R" || t === " ") root.toggleRecording()
        else if (t === "e" || t === "E") root.enhance("latest")
        else if (t === "t" || t === "T") root.openTui()
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

          PanelHero {
            id: hero
            width: parent.width
            title: root.heroTitle
            meta: root.heroMeta
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.recording || root.meeting !== "" ? 1.0 : 0.5
            iconComponent: Component {
              Text {
                text: root.heroIcon
                color: root.recording ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                SequentialAnimation on opacity {
                  running: root.meeting !== "" && !root.recording
                  loops: Animation.Infinite
                  NumberAnimation { from: 1.0; to: 0.35; duration: 750; easing.type: Easing.InOutQuad }
                  NumberAnimation { from: 0.35; to: 1.0; duration: 750; easing.type: Easing.InOutQuad }
                }
              }
            }
            trailingControl: Component {
              ToggleSwitch {
                id: recSwitch
                visible: root.daemonUp
                checked: root.recording
                foreground: hero.foreground
                accent: root.urgent
                onToggled: root.toggleRecording()
                PanelToolTip {
                  visible: recSwitch.containsMouse
                  text: root.recording ? "Stop recording  (R)" : "Start recording  (R)"
                  fontFamily: hero.fontFamily
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

          // --- live transcript --------------------------------------------------
          PanelSeparator { visible: root.sessionDir !== ""; width: parent.width }

          Column {
            visible: root.sessionDir !== ""
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              width: parent.width
              text: root.recording ? "Live transcript" : "Last transcript"
              foreground: root.dim
              fontFamily: root.fontFamily
            }

            Text {
              visible: root.transcriptTail.length === 0
              width: parent.width
              text: root.recording ? "Listening… the first chunk lands after " + "the first 30 s." : "No transcript yet."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Repeater {
              model: root.transcriptTail
              delegate: RowLayout {
                width: column.width
                spacing: Style.space(6)
                Text {
                  text: modelData.t
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  Layout.alignment: Qt.AlignTop
                }
                Text {
                  visible: modelData.speaker !== ""
                  text: modelData.speaker + ":"
                  color: modelData.speaker === "Me" ? Color.accent : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  Layout.alignment: Qt.AlignTop
                }
                Text {
                  text: modelData.text
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                  Layout.fillWidth: true
                }
              }
            }
          }

          // --- recent meetings ----------------------------------------------------
          PanelSeparator { width: parent.width }

          Column {
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              width: parent.width
              text: "Recent meetings"
              foreground: root.dim
              fontFamily: root.fontFamily
            }

            Text {
              visible: root.sessions.length === 0
              width: parent.width
              text: "No meetings recorded yet."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Repeater {
              model: root.sessions
              delegate: CursorSurface {
                id: row
                width: column.width
                implicitHeight: Math.max(nameCol.implicitHeight + Style.space(10), Style.spacing.controlHeight)
                foreground: root.foreground

                RowLayout {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(8)
                  anchors.rightMargin: Style.space(4)
                  spacing: Style.space(6)

                  Column {
                    id: nameCol
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                      width: parent.width
                      text: String(modelData.name)
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                    }
                    Text {
                      text: Number(modelData.lines || 0) + " lines" + (modelData.enhanced ? " · enhanced" : "")
                      color: modelData.enhanced ? Color.accent : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }

                  PanelActionButton {
                    // nf-md-auto_fix
                    iconText: "󰁨"
                    tooltipText: modelData.enhanced ? "Re-run enhance" : "Enhance with your LLM"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    Layout.alignment: Qt.AlignVCenter
                    onClicked: root.enhance(String(modelData.path))
                  }
                  PanelActionButton {
                    // nf-md-folder_open
                    iconText: "󰝰"
                    tooltipText: "Open folder"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    Layout.alignment: Qt.AlignVCenter
                    onClicked: root.openFolder(String(modelData.path))
                  }
                }
              }
            }
          }

          // --- footer -------------------------------------------------------------
          PanelSeparator { width: parent.width }

          RowLayout {
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: "Open TUI"
              iconText: ""
              tooltipText: "Full terminal UI: notes pane, /commands  (T)"
              foreground: root.foreground
              fontSize: Style.font.bodySmall
              bordered: true
              Layout.fillWidth: true
              onClicked: root.openTui()
            }
            Button {
              text: "Enhance latest"
              iconText: "󰁨"
              tooltipText: "muesli enhance --session latest  (E)"
              foreground: root.foreground
              fontSize: Style.font.bodySmall
              bordered: true
              enabled: root.sessions.length > 0
              Layout.fillWidth: true
              onClicked: root.enhance("latest")
            }
            PanelActionButton {
              iconText: "󰝰"
              tooltipText: "Open the notes folder"
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.openNotesDir()
            }
          }
        }
      }
    }
  }
}
