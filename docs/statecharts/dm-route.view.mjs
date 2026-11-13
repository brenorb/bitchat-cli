export const title = "Route a BitChat direct message";
const names = {ble: "live Bluetooth link", outbox: "Bluetooth outbox", nostr: "Nostr fallback"};
export const state = ({d}) => {
  if (d.phase === "choose") return "Checking the recipient and available routes";
  if (d.phase === "failed") return "Message submission failed";
  if (d.route === "none") return "No available route";
  return d.phase === "accepted" ? "Message accepted via " + names[d.route] : "Trying " + names[d.route];
};
export const step = (action, _from, to) => ({
  Route: to.d.route === "none" ? (to.d.valid ? "no authorized route" : "reject missing recipient or content") : "select " + names[to.d.route],
  Reject: "report unavailable route",
  Accept: "record outbound message and return its ID",
  SendError: "report send or record error",
  Done: "finish",
})[action];
