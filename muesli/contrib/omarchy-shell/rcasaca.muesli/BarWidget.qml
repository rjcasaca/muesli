import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// muesli bar widget for omarchy-shell. Equivalent of muesli/contrib/muesli.jsonc
// for Waybar: polls `muesli status --waybar` and reuses its JSON verbatim, so the
// daemon stays the single source of truth for what the bar says.
//
//   ○            idle
//   ◉ Teams      a meeting app holds the microphone (pulses)
//   ● 12:34      recording, elapsed time
BarWidget {
  id: root
  moduleName: "rcasaca.muesli"

  readonly property int pollSeconds: Math.max(1, Number(root.setting("pollSeconds", 2)))
  readonly property bool hideWhenIdle: root.setting("hideWhenIdle", false) === true
  readonly property bool hideWhenOff: root.setting("hideWhenOff", true) === true

  // "off" | "idle" | "meeting" | "recording"
  property string state: "off"
  property string label: ""
  property string tooltip: "muesli"

  readonly property bool recording: state === "recording"
  readonly property bool meeting: state === "meeting"

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function run(command) {
    if (root.bar) root.bar.run(command)
    // The daemon changes state asynchronously; poll again shortly after a click.
    afterClick.restart()
  }

  function toggle() { run("muesli toggle") }
  function enhance() { run("muesli enhance --session latest") }
  function openTui() { run("omarchy-launch-or-focus-tui muesli tui") }

  visible: !(state === "off" && hideWhenOff) && !(state === "idle" && hideWhenIdle)
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  IpcHandler {
    target: "rcasaca.muesli"
    function refresh(): void { root.refresh() }
    function toggle(): void { root.toggle() }
    function enhance(): void { root.enhance() }
  }

  Process {
    id: statusProc
    command: ["muesli", "status", "--waybar"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var st
        try { st = JSON.parse(text || "{}") } catch (e) { st = {} }
        var cls = String(st["class"] || "off")
        root.state = ["idle", "meeting", "recording"].indexOf(cls) !== -1 ? cls : "off"
        root.label = String(st.text || "")
        root.tooltip = String(st.tooltip || "muesli")
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) { root.state = "off"; root.label = ""; root.tooltip = "muesli daemon not running" }
    }
  }

  Timer {
    interval: root.pollSeconds * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: afterClick
    interval: 400
    onTriggered: root.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.label
    fontSize: Style.font.caption
    horizontalMargin: 6
    tooltipText: root.tooltip
    // Recording borrows the bar's "active/urgent" colour, like Waybar's red.
    active: root.recording
    dimmed: root.state === "idle"

    // Pulse while a meeting is detected but nothing is being recorded.
    SequentialAnimation on opacity {
      running: root.meeting
      loops: Animation.Infinite
      NumberAnimation { from: 1.0; to: 0.4; duration: 750; easing.type: Easing.InOutQuad }
      NumberAnimation { from: 0.4; to: 1.0; duration: 750; easing.type: Easing.InOutQuad }
      onRunningChanged: if (!running) button.opacity = 1.0
    }

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.LeftButton) root.toggle()
      else if (mouseButton === Qt.RightButton) root.enhance()
      else if (mouseButton === Qt.MiddleButton) root.openTui()
    }
  }
}
