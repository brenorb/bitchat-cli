export const title = "Receive and save a BitChat file";
export const state = ({f}) => ({
  receive: f.firstFailed ? "Receiving a retry" : "Receiving a file",
  save: "Saving a local copy",
  rejected: "File rejected",
  retry: "File could not be saved",
  duplicate: "Retry suppressed, file still missing",
  saved: "Local copy saved",
  ack: "Sending a private delivery receipt",
  finished: "File available in history",
})[f.phase];
export const step = (action, _from, to) => ({
  Validate: to.f.phase === "rejected" ? "reject invalid file" : to.f.phase === "duplicate" ? "suppress already seen transfer" : "accept verified file",
  Save: "write file",
  SaveError: "encounter filesystem error",
  Retry: "see the transfer again",
  Log: "record the saved file",
  Acknowledge: "finish the receipt attempt",
  Done: "finish",
})[action];
