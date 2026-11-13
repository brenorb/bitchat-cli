---------------------------- MODULE GeoPrecision ----------------------------
CONSTANT RequireForce
VARIABLE g
Init ==
    \E precision \in {"short", "medium", "high"}, force, powOk \in BOOLEAN,
       operation \in {"send", "watch"} :
        g = [phase |-> "validate", precision |-> precision, force |-> force,
             powOk |-> powOk, operation |-> operation, warning |-> FALSE]
Validate ==
    /\ g.phase = "validate"
    /\ g' = [g EXCEPT
       !.warning = (~g.force /\ g.precision # "short"),
       !.phase = IF (g.precision = "high" /\ ~g.force /\ RequireForce)
                   \/ (g.operation = "send" /\ ~g.powOk)
                 THEN "rejected" ELSE "dispatch"]
Call ==
    /\ g.phase = "dispatch"
    /\ \E outcome \in {"queued", "failed"} :
        g' = [g EXCEPT !.phase = outcome]
Done == g.phase \in {"queued", "failed", "rejected"} /\ UNCHANGED g
Next == Validate \/ Call \/ Done
HighPrecisionRequiresForce == g.phase \in {"dispatch", "queued"} => g.precision # "high" \/ g.force
=============================================================================
