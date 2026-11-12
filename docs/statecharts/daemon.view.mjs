export const title = "Start, check, and stop the BitChat daemon";
export const state = ({s}) => {
  if (s.child === "stopping") return s.caller === "stopping" ? "Waiting for daemon exit" : "Daemon still shutting down";
  if (s.caller === "starting") {
    return {
      spawned: "Waiting for command socket",
      initializing: "Socket open, services starting",
      ready: "Services ready, waiting for the reply",
      exited: "Child exited before startup completed",
    }[s.child];
  }
  if (s.child === "ready") return "Daemon running";
  return {
    stopped: "Daemon stopped",
    "still shutting down": "Daemon exited after the timeout",
    busy: "Radio already in use",
    failed: "Startup failed",
    none: "Daemon not running",
  }[s.result] ?? (s.child === "absent" ? "Daemon stopped" : "Startup reported before readiness");
};
export const step = (action) => ({
  Start: "start daemon",
  RadioBusy: "start while another session owns the radio",
  OpenSocket: "open command socket",
  Ready: "finish starting services",
  StartReport: "return startup result",
  RepeatStart: "start again",
  ExitDuringStart: "child exits",
  StartFails: "startup fails or times out",
  StopRequest: "request stop",
  StopDeadline: "stop wait expires",
  ExitAfterStop: "daemon exits",
  Done: "finish",
})[action];
