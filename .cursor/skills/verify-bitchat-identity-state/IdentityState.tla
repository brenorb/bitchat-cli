--------------------------- MODULE IdentityState ----------------------------
CONSTANTS ValidateOptional, AtomicSave
VARIABLE s
Init ==
    \E input \in {"absent", "legacy", "current", "malformed"} :
        s = [phase |-> "load", input |-> input, disk |-> input, identity |-> "none"]
Load ==
    /\ s.phase = "load"
    /\ s' = [s EXCEPT !.phase =
        CASE s.input = "current" -> "ready"
          [] s.input = "malformed" /\ ValidateOptional -> "rejected"
          [] OTHER -> "write",
        !.identity =
        CASE s.input = "absent" \/ (s.input = "malformed" /\ ~ValidateOptional) -> "generated"
          [] s.input \in {"legacy", "current"} -> "preserved"
          [] OTHER -> "none"]
Save ==
    /\ s.phase = "write"
    /\ s' = [s EXCEPT !.phase = "ready", !.disk = "current"]
SaveError ==
    /\ s.phase = "write"
    /\ s' = [s EXCEPT !.phase = "failed", !.disk = IF AtomicSave THEN s.input ELSE "partial"]
Done == s.phase \in {"ready", "rejected", "failed"} /\ UNCHANGED s
Next == Load \/ Save \/ SaveError \/ Done
MalformedPreserved == s.input = "malformed" => s.disk = "malformed" /\ s.identity = "none"
FailedSavePreservesFile == s.phase = "failed" => s.disk = s.input
MigrationKeepsIdentity == s.input = "legacy" /\ s.phase = "ready" => s.identity = "preserved"
=============================================================================
