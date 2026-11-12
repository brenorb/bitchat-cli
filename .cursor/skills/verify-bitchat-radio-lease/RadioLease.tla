------------------------------- MODULE RadioLease -------------------------------

CONSTANT Sessions
VARIABLES owner, session, seen

vars == <<owner, session, seen>>

Init ==
    /\ owner \in {"none", "stale"}
    /\ session = [p \in Sessions |-> "not checked"]
    /\ seen = [p \in Sessions |-> "none"]

ReadOwner ==
    \E p \in Sessions:
        /\ session[p] = "not checked"
        /\ session' = [session EXCEPT ![p] = "checked"]
        /\ seen' = [seen EXCEPT ![p] = owner]
        /\ UNCHANGED owner

RefuseExistingOwner ==
    \E p \in Sessions:
        /\ session[p] = "checked" /\ seen[p] \in Sessions /\ seen[p] # p
        /\ session' = [session EXCEPT ![p] = "refused"]
        /\ UNCHANGED <<owner, seen>>

WriteOwnPid ==
    \E p \in Sessions:
        /\ session[p] = "checked"
        /\ seen[p] \in {"none", "stale", p}
        /\ owner' = p
        /\ session' = [session EXCEPT ![p] = "lease acquired"]
        /\ UNCHANGED seen

ReadForRelease ==
    \E p \in Sessions:
        /\ session[p] = "lease acquired"
        /\ session' = [session EXCEPT ![p] = "releasing"]
        /\ seen' = [seen EXCEPT ![p] = owner]
        /\ UNCHANGED owner

Release ==
    \E p \in Sessions:
        /\ session[p] = "releasing"
        /\ owner' = IF seen[p] = p THEN "none" ELSE owner
        /\ session' = [session EXCEPT ![p] = "ended"]
        /\ UNCHANGED seen

Done ==
    /\ \A p \in Sessions: session[p] \in {"refused", "ended"}
    /\ UNCHANGED vars

Next == ReadOwner \/ RefuseExistingOwner \/ WriteOwnPid \/ ReadForRelease \/ Release \/ Done

OneSessionAcquiresLease ==
    \A p, q \in Sessions:
        (session[p] = "lease acquired" /\ session[q] = "lease acquired") => p = q

=============================================================================
