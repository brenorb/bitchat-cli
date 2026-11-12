------------------------------ MODULE Daemon ------------------------------
CONSTANTS WaitForReady, ProbeCached
VARIABLE s
Init == s = [child |-> "absent", caller |-> "idle", record |-> "missing",
             radio |-> "free", result |-> "none", readyObserved |-> FALSE]

Start ==
    /\ s.caller = "idle" /\ s.child = "absent" /\ s.radio = "free"
    /\ s' = [s EXCEPT !.child = "spawned", !.caller = "starting",
             !.record = "live", !.result = "none", !.readyObserved = FALSE]
RadioBusy ==
    /\ s.child = "absent" /\ s.caller = "idle"
    /\ s' = [s EXCEPT !.radio = "other", !.result = "busy", !.caller = "reported"]
OpenSocket ==
    /\ s.child = "spawned"
    /\ s' = [s EXCEPT !.child = "initializing", !.radio = "daemon"]
Ready ==
    /\ s.child = "initializing"
    /\ s' = [s EXCEPT !.child = "ready"]
StartReport ==
    /\ s.caller = "starting"
    /\ IF WaitForReady THEN s.child = "ready" ELSE s.child # "absent"
    /\ s' = [s EXCEPT !.caller = "reported", !.result = "started",
             !.readyObserved = WaitForReady]
RepeatStart ==
    /\ s.record = "live" /\ s.child \in {"spawned", "initializing", "ready", "stopping"}
    /\ s.caller \in {"starting", "reported"}
    /\ LET observed == ProbeCached /\ s.child \in {"ready", "stopping"} IN
       s' = [s EXCEPT !.result = IF observed \/ (~ProbeCached /\ s.radio # "daemon")
                                 THEN "already running" ELSE "not ready",
              !.readyObserved = observed]
ExitDuringStart ==
    /\ s.child \in {"spawned", "initializing"}
    /\ s' = [s EXCEPT !.child = "exited", !.record = "stale", !.radio = "free"]
StartFails ==
    /\ s.caller = "starting" /\ WaitForReady
    /\ s' = [s EXCEPT !.child = "absent", !.record = "missing", !.radio = "free",
             !.caller = "reported", !.result = "failed", !.readyObserved = FALSE]
StopRequest ==
    /\ s.caller = "reported" /\ s.child = "ready"
    /\ s' = [s EXCEPT !.child = "stopping", !.caller = "stopping",
             !.result = "none", !.readyObserved = FALSE]
StopDeadline ==
    /\ s.caller = "stopping" /\ s.child = "stopping"
    /\ s' = [s EXCEPT !.result = "still shutting down", !.caller = "reported"]
ExitAfterStop ==
    /\ s.child = "stopping"
    /\ s' = [s EXCEPT !.child = "absent", !.record = "missing", !.radio = "free",
             !.caller = "reported", !.result = IF s.caller = "stopping" THEN "stopped" ELSE @]
Done ==
    /\ s.caller = "reported"
    /\ UNCHANGED s
Next == Start \/ RadioBusy \/ OpenSocket \/ Ready \/ StartReport \/ RepeatStart
        \/ ExitDuringStart \/ StartFails \/ StopRequest \/ StopDeadline \/ ExitAfterStop \/ Done
SuccessRequiresReadyReply ==
    s.result \in {"started", "already running"} => s.readyObserved
=============================================================================
