----------------------------- MODULE WriteBuffer ------------------------------
CONSTANT ClearOnUnsubscribe
VARIABLE b
Init ==
    b = [phase |-> "connected", buffered |-> FALSE, fromEarlierLink |-> FALSE,
         staleDelivery |-> FALSE]
Partial ==
    /\ b.phase = "connected" /\ ~b.buffered
    /\ b' = [b EXCEPT !.buffered = TRUE]
Complete ==
    /\ b.phase = "connected"
    /\ b' = [b EXCEPT !.staleDelivery = @ \/ b.fromEarlierLink, !.buffered = FALSE,
                       !.fromEarlierLink = FALSE]
Oversized ==
    /\ b.phase = "connected"
    /\ b' = [b EXCEPT !.buffered = FALSE, !.fromEarlierLink = FALSE]
Unsubscribe ==
    /\ b.phase = "connected"
    /\ b' = [b EXCEPT !.phase = "disconnected", !.buffered = b.buffered /\ ~ClearOnUnsubscribe,
                       !.fromEarlierLink = b.buffered /\ ~ClearOnUnsubscribe]
Subscribe ==
    /\ b.phase = "disconnected"
    /\ b' = [b EXCEPT !.phase = "connected"]
Done == UNCHANGED b
Next == Partial \/ Complete \/ Oversized \/ Unsubscribe \/ Subscribe \/ Done
NoStaleDelivery == ~b.staleDelivery
=============================================================================
