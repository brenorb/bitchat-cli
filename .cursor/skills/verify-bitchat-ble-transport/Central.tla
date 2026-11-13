------------------------------- MODULE Central -------------------------------
CONSTANT CleanupCancelled
VARIABLE b
Init ==
    b = [phase |-> "scanning", running |-> TRUE, physical |-> FALSE, tracked |-> FALSE]
Discover ==
    /\ b.phase = "scanning"
    /\ b' = [b EXCEPT !.phase = "queued"]
StartConnect ==
    /\ b.phase = "queued"
    /\ b' = [b EXCEPT !.phase = "connecting"]
LinkUp ==
    /\ b.phase = "connecting"
    /\ b' = [b EXCEPT !.phase = "subscribe", !.physical = TRUE]
Ready ==
    /\ b.phase = "subscribe"
    /\ b' = [b EXCEPT !.phase = "connected", !.tracked = TRUE]
Fail ==
    /\ b.phase \in {"connecting", "subscribe"}
    /\ b' = [b EXCEPT !.phase = "backoff", !.physical = FALSE]
Disconnect ==
    /\ b.phase = "connected"
    /\ b' = [b EXCEPT !.phase = "backoff", !.physical = FALSE, !.tracked = FALSE]
Retry ==
    /\ b.phase = "backoff"
    /\ b' = [b EXCEPT !.phase = "scanning"]
Stop ==
    /\ b.running
    /\ b' = [b EXCEPT !.phase = "stopped", !.running = FALSE, !.tracked = FALSE,
           !.physical = b.physical /\ ~b.tracked /\ ~CleanupCancelled]
Done == b.phase = "stopped" /\ UNCHANGED b
Next == Discover \/ StartConnect \/ LinkUp \/ Ready \/ Fail \/ Disconnect \/ Retry \/ Stop \/ Done
TrackedOnlyAfterNotify == b.tracked => b.phase = "connected" /\ b.physical
StopDisconnectsLink == ~b.running => ~b.physical
=============================================================================
