export const title = "Send a BitChat geohash message or add a watch";
export const state = ({g}) => ({
  validate: "Checking the geohash and command options",
  rejected: "Command rejected before dispatch",
  dispatch: g.warning ? "Privacy warning shown, dispatching" : "Dispatching to daemon",
  queued: "Daemon accepted the request",
  failed: "Daemon request failed",
})[g.phase];
export const step = (action, _from, to) => ({
  Validate: to.g.phase === "rejected" ? "require force or reject invalid options" : "accept the command",
  Call: to.g.phase === "queued" ? "return daemon success" : "report daemon failure",
  Done: "finish",
})[action];
