export const title = "BitChat foreground and daemon radio ownership";

export function state({ session }) {
  const phases = Object.values(session);
  const acquired = phases.filter(phase => phase === "lease acquired").length;
  if (acquired === 2) return "Both sessions are allowed to start the radio";
  if (acquired === 1) return "One session can start the radio";
  if (phases.includes("releasing")) return "The session is closing";
  if (phases.every(phase => ["ended", "refused"].includes(phase))) return "No session is using the radio";
  const checked = phases.filter(phase => phase === "checked").length;
  if (checked === 2) return "Both sessions checked radio ownership";
  if (checked === 1) return "One session checked radio ownership";
  return "Sessions are about to start";
}

export function step(action) {
  switch (action) {
    case "ReadOwner": return "a session checks for an existing owner";
    case "RefuseExistingOwner": return "the existing owner is still running";
    case "WriteOwnPid": return "a session records its ownership";
    case "ReadForRelease": return "a session begins shutting down";
    case "Release": return "the session releases the radio";
    case "Done": return "the sessions have exited";
    default: throw new Error(`Unexpected action ${action}`);
  }
}
