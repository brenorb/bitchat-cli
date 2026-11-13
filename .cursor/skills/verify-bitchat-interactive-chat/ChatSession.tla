------------------------------ MODULE ChatSession ------------------------------
CONSTANT ReleaseOnLoadError
VARIABLE c
Init == c = [phase |-> "start", lease |-> FALSE, transport |-> FALSE, stdin |-> FALSE]
Acquire == c.phase = "start" /\ c' = [c EXCEPT !.phase = "load", !.lease = TRUE]
Busy == c.phase = "start" /\ c' = [c EXCEPT !.phase = "done"]
Load == c.phase = "load" /\ c' = [c EXCEPT !.phase = "transport"]
LoadError ==
    /\ c.phase = "load"
    /\ c' = [c EXCEPT !.phase = "done", !.lease = ~ReleaseOnLoadError]
StartTransport ==
    /\ c.phase = "transport"
    /\ c' = [c EXCEPT !.phase = "open input", !.transport = TRUE]
OpenInput ==
    /\ c.phase = "open input"
    /\ c' = [c EXCEPT !.phase = "prompt", !.stdin = TRUE]
Input ==
    /\ c.phase = "prompt"
    /\ \E next \in {"prompt", "public send", "private send", "cleanup"} :
        c' = [c EXCEPT !.phase = next]
Sent ==
    /\ c.phase \in {"public send", "private send"}
    /\ c' = [c EXCEPT !.phase = "prompt"]
Interrupt ==
    /\ c.phase \in {"transport", "open input", "prompt", "public send", "private send"}
    /\ c' = [c EXCEPT !.phase = "cleanup"]
Cleanup ==
    /\ c.phase = "cleanup"
    /\ c' = [c EXCEPT !.phase = "done", !.lease = FALSE, !.transport = FALSE, !.stdin = FALSE]
Done == c.phase = "done" /\ UNCHANGED c
Next == Acquire \/ Busy \/ Load \/ LoadError \/ StartTransport \/ OpenInput \/ Input \/ Sent \/ Interrupt \/ Cleanup \/ Done
EndedWithoutResources == c.phase = "done" => ~c.lease /\ ~c.transport /\ ~c.stdin
SendRequiresSession == c.phase \in {"public send", "private send"} => c.lease /\ c.transport /\ c.stdin
=============================================================================
