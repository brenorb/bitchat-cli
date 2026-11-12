------------------------------- MODULE Fragments -------------------------------
EXTENDS Naturals, FiniteSets
CONSTANT PruneExpired
VARIABLE f
Empty == [indices |-> {}, age |-> 0]
Init == f = [active |-> 0, entry |-> Empty, returnedExpired |-> FALSE]
Tick ==
    /\ f.active # 0 /\ f.entry.age < 2
    /\ f' = [f EXCEPT !.entry.age = @ + 1]
Append ==
    \E key, index \in 1..2 :
        LET keep == key = f.active /\ (~PruneExpired \/ f.entry.age < 2)
            entry == IF keep THEN f.entry ELSE Empty
            indices == entry.indices \cup {index}
            complete == Cardinality(indices) = 2
        IN f' = [f EXCEPT !.active = IF complete THEN 0 ELSE key,
                 !.entry = IF complete THEN Empty ELSE [entry EXCEPT !.indices = indices],
                 !.returnedExpired = (@ \/ (complete /\ entry.age = 2))]
DiscardMismatch ==
    /\ f.active # 0 /\ (~PruneExpired \/ f.entry.age < 2)
    /\ f' = [f EXCEPT !.active = 0, !.entry = Empty]
Expire ==
    /\ f.active # 0 /\ f.entry.age = 2
    /\ f' = [f EXCEPT !.active = 0, !.entry = Empty]
Next == Tick \/ Append \/ DiscardMismatch \/ Expire
NeverReassembleExpired == ~f.returnedExpired
=============================================================================
