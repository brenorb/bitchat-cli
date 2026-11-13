------------------------------ MODULE AppendIndex ------------------------------
EXTENDS Naturals
CONSTANT Rebuild
VARIABLES indexed, known, count
Init == indexed = FALSE /\ known = FALSE /\ count = 0
Append ==
    /\ count < 2
    /\ LET alreadyKnown == IF indexed THEN known ELSE Rebuild /\ count > 0 IN
        count' = IF alreadyKnown THEN count ELSE count + 1
    /\ indexed' = TRUE /\ known' = TRUE
Restart ==
    /\ indexed
    /\ indexed' = FALSE /\ known' = FALSE
    /\ UNCHANGED count
Idle == UNCHANGED <<indexed, known, count>>
Next == Append \/ Restart \/ Idle
AtMostOneStableRecord == count <= 1
=============================================================================
