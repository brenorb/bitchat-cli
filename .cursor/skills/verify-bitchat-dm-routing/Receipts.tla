------------------------------- MODULE Receipts -------------------------------
CONSTANT SkipSent
VARIABLE r
Init ==
    \E json, routed \in BOOLEAN :
        r = [phase |-> "view", json |-> json, routed |-> routed,
             alreadySent |-> FALSE, repeated |-> FALSE]
View ==
    /\ r.phase = "view"
    /\ r' = [r EXCEPT !.phase = IF r.json \/ (SkipSent /\ r.alreadySent)
                                 THEN "returned" ELSE "attempt"]
Record ==
    /\ r.phase = "attempt" /\ r.routed
    /\ r' = [r EXCEPT !.phase = "returned", !.alreadySent = TRUE,
                       !.repeated = @ \/ r.alreadySent]
NoRoute ==
    /\ r.phase = "attempt"
    /\ r' = [r EXCEPT !.phase = "returned"]
ReadAgain ==
    /\ r.phase = "returned"
    /\ \E routed \in BOOLEAN :
        r' = [r EXCEPT !.phase = "view", !.routed = routed]
Done == r.phase = "returned" /\ UNCHANGED r
Next == View \/ Record \/ NoRoute \/ ReadAgain \/ Done
JsonViewDoesNotSend == r.json => ~r.alreadySent
NoDuplicateReceipt == ~r.repeated
=============================================================================
