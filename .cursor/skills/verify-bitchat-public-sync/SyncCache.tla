------------------------------ MODULE SyncCache ------------------------------
EXTENDS Naturals, Sequences
CONSTANT SuppressKnown
VARIABLES cache, sent, redundant
Packets == {"one", "two", "three"}
Init == cache = <<>> /\ sent = "none" /\ redundant = FALSE
Remember ==
    /\ \E p \in Packets :
        cache' = IF \E i \in 1..Len(cache) : cache[i] = p
                 THEN cache
                 ELSE Append(IF Len(cache) = 2 THEN Tail(cache) ELSE cache, p)
    /\ UNCHANGED <<sent, redundant>>
Reply ==
    /\ \E i \in 1..Len(cache), receiverHas \in BOOLEAN, requested \in BOOLEAN :
        /\ requested /\ (~receiverHas \/ ~SuppressKnown)
        /\ sent' = cache[i]
        /\ redundant' = receiverHas
    /\ UNCHANGED cache
Idle == UNCHANGED <<cache, sent, redundant>>
Next == Remember \/ Reply \/ Idle
CacheBound == Len(cache) <= 2
NoKnownReply == ~redundant
=============================================================================
