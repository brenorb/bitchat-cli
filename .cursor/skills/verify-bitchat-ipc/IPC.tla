-------------------------------- MODULE IPC --------------------------------
CONSTANTS RequireNewline, RequireIdentifier, HasTimeout
VARIABLE r
Init ==
    \E identified \in BOOLEAN :
        r = [stage |-> "connect", server |-> "idle", frame |-> "empty",
             identified |-> identified, result |-> "none", closed |-> FALSE]
NoSocket ==
    /\ r.stage = "connect"
    /\ r' = [r EXCEPT !.stage = "done", !.result = "no socket", !.closed = TRUE]
SendRequest ==
    /\ r.stage = "connect"
    /\ r' = [r EXCEPT !.stage = "waiting", !.server = "received"]
QueueRequest ==
    /\ r.server = "received"
    /\ r' = [r EXCEPT !.server = "queued"]
ReadPartial ==
    /\ r.stage = "waiting" /\ r.frame = "empty" /\ r.server = "queued"
    /\ r' = [r EXCEPT !.frame = "partial"]
ReadReply ==
    /\ r.stage = "waiting" /\ r.server = "queued"
    /\ \E frame \in {"complete with id", "complete without id", "invalid", "rejected"} :
          r' = [r EXCEPT !.stage = "decoding", !.frame = frame, !.closed = TRUE]
EOF ==
    /\ r.stage = "waiting"
    /\ r' = [r EXCEPT !.stage = "decoding", !.closed = TRUE]
SocketError ==
    /\ r.stage \in {"connect", "waiting"}
    /\ r' = [r EXCEPT !.stage = "done", !.result = "socket error", !.closed = TRUE]
Timeout ==
    /\ HasTimeout /\ r.stage \in {"connect", "waiting"}
    /\ r' = [r EXCEPT !.stage = "done", !.result = "timeout", !.closed = TRUE]
Decode ==
    /\ r.stage = "decoding"
    /\ LET framed == r.frame \in {"complete with id", "complete without id"} \/
                        (r.frame = "partial" /\ ~RequireNewline)
           validId == r.frame = "complete with id"
       IN r' = [r EXCEPT !.stage = "done",
                !.result = IF framed /\ (~r.identified \/ validId \/ ~RequireIdentifier)
                            THEN "accepted" ELSE "rejected"]
Done == r.stage = "done" /\ UNCHANGED r
Next == NoSocket \/ SendRequest \/ QueueRequest \/ ReadPartial \/ ReadReply \/
        EOF \/ SocketError \/ Timeout \/ Decode \/ Done
AcceptanceNeedsFrame ==
    r.result = "accepted" => r.frame \in {"complete with id", "complete without id"}
IdentifiedAcceptanceNeedsId ==
    (r.result = "accepted" /\ r.identified) => r.frame = "complete with id"
=============================================================================
