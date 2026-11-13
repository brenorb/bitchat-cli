----------------------------- MODULE RelayDirectory ----------------------------
CONSTANT RefreshInvalid
VARIABLE c
Init ==
    \E contents \in {"missing", "empty", "invalid", "valid"} :
        c = [phase |-> "load", disk |-> contents, fetched |-> FALSE, refreshed |-> FALSE]
Load ==
    /\ c.phase = "load"
    /\ c' = [c EXCEPT !.phase = IF c.disk \in {"missing", "empty"} THEN "download" ELSE "parse"]
Download ==
    /\ c.phase = "download"
    /\ \E disk \in {"valid", "invalid"} :
        c' = [c EXCEPT !.phase = "parse", !.disk = disk, !.fetched = TRUE]
Parse ==
    /\ c.phase = "parse"
    /\ c' = [c EXCEPT !.phase = CASE c.disk = "valid" -> "done"
                              [] RefreshInvalid /\ ~c.refreshed -> "refresh"
                              [] OTHER -> "done"]
Refresh ==
    /\ c.phase = "refresh"
    /\ c' = [c EXCEPT !.phase = "download", !.refreshed = TRUE, !.disk = "missing"]
Fail == c.phase = "download" /\ c' = [c EXCEPT !.phase = "failed"]
Done == c.phase \in {"done", "failed"} /\ UNCHANGED c
Next == Load \/ Download \/ Parse \/ Refresh \/ Fail \/ Done
InvalidResultAfterRefresh == c.phase = "done" /\ c.disk # "valid" => c.refreshed
=============================================================================
