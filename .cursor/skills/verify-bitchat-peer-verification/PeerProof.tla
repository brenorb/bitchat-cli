------------------------------ MODULE PeerProof -------------------------------
CONSTANT ComparePeer
VARIABLE p
Init ==
    \E target, proof, signed, keyMatch \in BOOLEAN :
        p = [phase |-> "resolve", target |-> target, proof |-> proof,
             signed |-> signed, keyMatch |-> keyMatch, marked |-> FALSE]
Resolve ==
    /\ p.phase = "resolve"
    /\ p' = [p EXCEPT !.phase =
        CASE ~p.target -> "failed"
          [] p.proof -> "check"
          [] OTHER -> "write"]
Check ==
    /\ p.phase = "check"
    /\ p' = [p EXCEPT !.phase = IF p.signed /\ (p.keyMatch \/ ~ComparePeer)
                                  THEN "write" ELSE "failed"]
Write == p.phase = "write" /\ p' = [p EXCEPT !.phase = "done", !.marked = TRUE]
Done == p.phase \in {"done", "failed"} /\ UNCHANGED p
Next == Resolve \/ Check \/ Write \/ Done
ProofMustMatchPeer == p.marked /\ p.proof => p.signed /\ p.keyMatch
=============================================================================
