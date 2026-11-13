------------------------------ MODULE NoteQuery -------------------------------
CONSTANT KeepPartial
VARIABLE n
Init == n = [phase |-> "read", received |-> FALSE, result |-> FALSE]
Receive ==
    /\ n.phase = "read" /\ ~n.received
    /\ n' = [n EXCEPT !.received = TRUE]
EndOfStoredEvents ==
    /\ n.phase = "read"
    /\ n' = [n EXCEPT !.phase = "finished", !.result = n.received]
Timeout ==
    /\ n.phase = "read"
    /\ n' = [n EXCEPT !.phase = "finished", !.result = n.received /\ KeepPartial]
Done == n.phase = "finished" /\ UNCHANGED n
Next == Receive \/ EndOfStoredEvents \/ Timeout \/ Done
ReceivedNoteRetained == (n.phase = "finished" /\ n.received) => n.result
NoNoteWithoutEvent == n.result => n.received
=============================================================================
