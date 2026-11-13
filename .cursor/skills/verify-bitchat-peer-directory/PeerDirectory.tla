----------------------------- MODULE PeerDirectory -----------------------------
EXTENDS Naturals
CONSTANTS KeepNewest, PreserveOmitted
VARIABLE p
Init ==
    p = [time |-> 0, maxSeen |-> 0, linked |-> FALSE, favorite |-> FALSE,
         blocked |-> FALSE, verified |-> FALSE, omittedChanged |-> FALSE]
Observe ==
    \E timestamp \in 1..3 :
        p' = [p EXCEPT !.time = IF ~KeepNewest \/ timestamp >= p.time THEN timestamp ELSE p.time,
                       !.maxSeen = IF timestamp > p.maxSeen THEN timestamp ELSE p.maxSeen]
UpdateSocial ==
    \E update \in {"link", "favorite", "block", "verify"}, value \in BOOLEAN :
        p' = [p EXCEPT !.linked = IF update = "link" THEN value ELSE p.linked,
                       !.favorite = IF update = "favorite" THEN value ELSE p.favorite /\ PreserveOmitted,
                       !.blocked = IF update = "block" THEN value ELSE p.blocked,
                       !.verified = IF update = "verify" THEN value ELSE p.verified,
                       !.omittedChanged = @ \/ (update # "favorite" /\ p.favorite /\ ~PreserveOmitted)]
Unlink == p' = [p EXCEPT !.linked = FALSE, !.favorite = FALSE, !.blocked = FALSE, !.verified = FALSE]
Idle == UNCHANGED p
Next == Observe \/ UpdateSocial \/ Unlink \/ Idle
NewestObservationRetained == p.time = p.maxSeen
OmittedFavoritePreserved == ~p.omittedChanged
=============================================================================
