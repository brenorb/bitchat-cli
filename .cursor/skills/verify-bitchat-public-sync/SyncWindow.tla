----------------------------- MODULE SyncWindow ------------------------------
CONSTANT CheckWindow
VARIABLE s
Init ==
    s = [window |-> "closed", phase |-> "idle", checked |-> FALSE,
         accepted |-> FALSE]
Request ==
    /\ s.phase = "idle"
    /\ s' = [s EXCEPT !.window = "open", !.checked = FALSE, !.accepted = FALSE]
Expire ==
    /\ s.phase = "idle" /\ s.window = "open"
    /\ s' = [s EXCEPT !.window = "expired", !.checked = FALSE, !.accepted = FALSE]
Respond ==
    /\ s.phase = "idle"
    /\ \E samePeer \in BOOLEAN, knownIngress \in BOOLEAN :
        s' = [s EXCEPT !.checked = s.window = "open" /\ samePeer,
                       !.accepted = knownIngress /\ (~CheckWindow \/ (s.window = "open" /\ samePeer))]
Idle == UNCHANGED s
Next == Request \/ Expire \/ Respond \/ Idle
ResponseRequiresRecentRequest == s.accepted => s.checked
=============================================================================
