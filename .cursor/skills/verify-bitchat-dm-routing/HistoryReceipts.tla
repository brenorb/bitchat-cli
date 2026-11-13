-------------------------- MODULE HistoryReceipts -----------------------------
EXTENDS Naturals
CONSTANTS Recheck, FullHistory
VARIABLES rows, sent, calls
Readers == {"first", "second"}
Init ==
    /\ rows \in BOOLEAN /\ sent = IF rows THEN 1 ELSE 0
    /\ calls = [reader \in Readers |-> "view"]
View ==
    \E reader \in Readers :
        /\ calls[reader] = "view"
        /\ calls' = [calls EXCEPT ![reader] = IF sent = 0 \/ ~FullHistory THEN "dispatch" ELSE "done"]
        /\ UNCHANGED <<rows, sent>>
Dispatch ==
    \E reader \in Readers :
        /\ calls[reader] = "dispatch"
        /\ sent' = IF Recheck /\ sent > 0 THEN sent ELSE sent + 1
        /\ calls' = [calls EXCEPT ![reader] = "done"]
        /\ UNCHANGED rows
Done == (\A reader \in Readers : calls[reader] = "done") /\ UNCHANGED <<rows, sent, calls>>
Next == View \/ Dispatch \/ Done
NoRepeatedReadReceipt == sent <= 1
=============================================================================
