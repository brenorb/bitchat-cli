------------------------------ MODULE Incoming ------------------------------
CONSTANT RememberBeforeSave
VARIABLE f
Init ==
    \E valid \in BOOLEAN, private \in BOOLEAN :
        f = [phase |-> "receive", valid |-> valid, private |-> private, seen |-> FALSE,
             saved |-> FALSE, logged |-> FALSE, receipt |-> FALSE, firstFailed |-> FALSE]
Validate ==
    /\ f.phase = "receive"
    /\ f' = [f EXCEPT !.phase =
        CASE ~f.valid -> "rejected"
          [] f.seen -> "duplicate"
          [] OTHER -> "save",
        !.seen = f.seen \/ (f.valid /\ RememberBeforeSave)]
Save ==
    /\ f.phase = "save"
    /\ f' = [f EXCEPT !.phase = "saved", !.saved = TRUE, !.seen = TRUE]
SaveError ==
    /\ f.phase = "save" /\ ~f.firstFailed
    /\ f' = [f EXCEPT !.phase = "retry", !.firstFailed = TRUE]
Retry ==
    /\ f.phase = "retry"
    /\ f' = [f EXCEPT !.phase = "receive"]
Log ==
    /\ f.phase = "saved"
    /\ f' = [f EXCEPT !.phase = IF f.private THEN "ack" ELSE "finished", !.logged = TRUE]
Acknowledge ==
    /\ f.phase = "ack"
    /\ \E sent \in BOOLEAN :
        f' = [f EXCEPT !.phase = "finished", !.receipt = sent]
Done == f.phase \in {"rejected", "duplicate", "finished"} /\ UNCHANGED f
Next == Validate \/ Save \/ SaveError \/ Retry \/ Log \/ Acknowledge \/ Done
OnlySavedFilesReported == (f.logged \/ f.receipt) => f.saved /\ f.valid
DuplicateImpliesSaved == f.phase = "duplicate" => f.saved
=============================================================================
