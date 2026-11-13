------------------------------ MODULE ProxyConfig -----------------------------
CONSTANT CheckWebsocket
VARIABLE p
Init ==
    \E transport \in {"http", "websocket"}, mode \in {"disabled", "system", "custom"},
       required \in BOOLEAN :
        p = [phase |-> "choose", transport |-> transport, mode |-> mode,
             required |-> required, selection |-> "none"]
Choose ==
    /\ p.phase = "choose"
    /\ p' = [p EXCEPT !.phase = "configured",
        !.selection = CASE p.mode = "disabled" /\ p.required
                            /\ (p.transport = "http" \/ CheckWebsocket) -> "rejected"
                         [] p.mode = "disabled" -> "direct"
                         [] p.mode = "system" -> "system"
                         [] OTHER -> "custom"]
Done == p.phase = "configured" /\ UNCHANGED p
Next == Choose \/ Done
DisabledRequiredRejected == p.phase = "configured" /\ p.required /\ p.mode = "disabled" =>
    p.selection = "rejected"
=============================================================================
