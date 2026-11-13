-------------------------------- MODULE Routes --------------------------------
CONSTANT RequireEligible
VARIABLE d
Init ==
    \E known, favorite, blocked, linked, valid \in BOOLEAN, link \in {"up", "down"} :
        /\ (link = "up" => known)
        /\ d = [phase |-> "choose", known |-> known, link |-> link, linked |-> linked,
                 favorite |-> favorite, blocked |-> blocked, valid |-> valid,
                 route |-> "none", recorded |-> FALSE]
Eligible == d.linked /\ d.favorite /\ ~d.blocked
Route ==
    /\ d.phase = "choose"
    /\ d' = [d EXCEPT !.phase = IF d.valid THEN "send" ELSE "failed",
        !.route = CASE ~d.valid -> "none"
                    [] d.link = "up" -> "ble"
                    [] d.known -> "outbox"
                    [] Eligible \/ (d.linked /\ ~RequireEligible) -> "nostr"
                    [] OTHER -> "none"]
Reject ==
    /\ d.phase = "send" /\ d.route = "none"
    /\ d' = [d EXCEPT !.phase = "failed"]
Accept ==
    /\ d.phase = "send" /\ d.route # "none"
    /\ d' = [d EXCEPT !.phase = "accepted", !.recorded = TRUE]
SendError ==
    /\ d.phase = "send" /\ d.route # "none"
    /\ d' = [d EXCEPT !.phase = "failed"]
Done == d.phase \in {"accepted", "failed"} /\ UNCHANGED d
Next == Route \/ Reject \/ Accept \/ SendError \/ Done
NostrRequiresEligiblePeer == d.route = "nostr" => Eligible /\ ~d.known
AcceptedIsRecorded == (d.phase = "accepted") = d.recorded
=============================================================================
