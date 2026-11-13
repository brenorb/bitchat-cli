------------------------------- MODULE Ingress ---------------------------------
CONSTANT Deduplicate
VARIABLES phase, accepted, kind, delivered
Kinds == {"not event", "invalid envelope", "valid message", "valid receipt"}
Init == phase = "receive" /\ accepted = FALSE /\ delivered = 0 /\ kind = "not event"
Receive ==
    /\ phase = "receive"
    /\ kind' \in Kinds
    /\ phase' = "check"
    /\ UNCHANGED <<accepted, delivered>>
Check ==
    /\ phase = "check"
    /\ phase' = IF kind \in {"valid message", "valid receipt"} /\ (~accepted \/ ~Deduplicate)
                 THEN "deliver" ELSE "receive"
    /\ UNCHANGED <<accepted, kind, delivered>>
Dispatch ==
    /\ phase = "deliver"
    /\ delivered' = IF accepted THEN 2 ELSE 1
    /\ accepted' = TRUE /\ phase' = "receive"
    /\ UNCHANGED kind
Next == Receive \/ Check \/ Dispatch
AtMostOneDispatch == delivered # 2
DispatchUsesValidPayload == phase = "deliver" => kind \in {"valid message", "valid receipt"}
=============================================================================
