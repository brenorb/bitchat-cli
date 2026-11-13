------------------------------ MODULE NoiseQueue ------------------------------
CONSTANT QueueBeforeHandshake
VARIABLE n
Init ==
    n = [phase |-> "new", pending |-> FALSE, handshake |-> FALSE, delivered |-> FALSE]
Submit ==
    /\ n.phase = "new"
    /\ n' = [n EXCEPT !.phase = "outbox", !.pending = QueueBeforeHandshake]
Attempt ==
    /\ n.phase = "outbox"
    /\ n' = [n EXCEPT !.phase = "handshaking", !.handshake = TRUE]
LinkMissing ==
    /\ n.phase = "handshaking"
    /\ n' = [n EXCEPT !.phase = "outbox", !.handshake = FALSE]
AcceptHandshake ==
    /\ n.phase = "handshaking"
    /\ n' = [n EXCEPT !.phase = "established"]
Send ==
    /\ n.phase = "established" /\ n.pending
    /\ n' = [n EXCEPT !.phase = "sent", !.pending = FALSE]
PauseFlush ==
    /\ n.phase = "established" /\ n.pending
    /\ UNCHANGED n
Expire ==
    /\ n.phase \in {"outbox", "handshaking", "established"}
    /\ n' = [n EXCEPT !.phase = "expired", !.pending = FALSE, !.handshake = FALSE]
Delivered ==
    /\ n.phase = "sent"
    /\ n' = [n EXCEPT !.phase = "received", !.delivered = TRUE]
Done == n.phase \in {"sent", "received", "expired"} /\ UNCHANGED n
Next == Submit \/ Attempt \/ LinkMissing \/ AcceptHandshake \/ Send \/ PauseFlush \/ Expire \/ Delivered \/ Done
QueueRetainsPending == n.phase \in {"outbox", "handshaking", "established"} => n.pending
SentAfterHandshake == n.phase \in {"sent", "received"} => n.handshake
=============================================================================
