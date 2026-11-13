----------------------------- MODULE PublishAck -----------------------------
EXTENDS Naturals
CONSTANT RequireAck
VARIABLE p
Init == p = [phase |-> "send", remaining |-> 2, acked |-> FALSE, sent |-> FALSE]
Send ==
    /\ p.phase = "send" /\ p.remaining > 0
    /\ p' = [p EXCEPT !.phase = IF RequireAck THEN "wait" ELSE "send",
                       !.remaining = @ - 1, !.sent = TRUE]
Ignore ==
    /\ p.phase = "wait"
    /\ UNCHANGED p
Ack ==
    /\ p.phase = "wait"
    /\ p' = [p EXCEPT !.phase = "send", !.acked = TRUE]
RejectOrTimeout ==
    /\ p.phase \in {"send", "wait"} /\ (p.phase = "wait" \/ p.remaining > 0)
    /\ p' = [p EXCEPT !.phase = "send",
          !.remaining = IF p.phase = "send" THEN @ - 1 ELSE @]
Finish ==
    /\ p.phase = "send" /\ p.remaining = 0
    /\ p' = [p EXCEPT !.phase = IF p.acked \/ (p.sent /\ ~RequireAck) THEN "ok" ELSE "failed"]
Done == p.phase \in {"ok", "failed"} /\ UNCHANGED p
Next == Send \/ Ignore \/ Ack \/ RejectOrTimeout \/ Finish \/ Done
SuccessRequiresAck == p.phase = "ok" => p.acked
=============================================================================
