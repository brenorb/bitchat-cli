export const title = "Discover and connect to a BitChat Bluetooth peer";
export const state = ({b}) => ({
  scanning: "Scanning for a peer",
  queued: "Peer discovered",
  connecting: "Connecting to peer",
  subscribe: "Connected, enabling notifications",
  connected: "Peer ready",
  backoff: "Waiting before reconnecting",
  stopped: b.physical ? "Transport stopped with link still open" : "Transport stopped",
})[b.phase];
export const step = (action) => ({
  Discover: "find peer advertisement",
  StartConnect: "start connecting",
  LinkUp: "open Bluetooth link",
  Ready: "enable notifications",
  Fail: "connection setup fails",
  Disconnect: "peer disconnects",
  Retry: "retry delay ends",
  Stop: "stop transport",
  Done: "finish",
})[action];
