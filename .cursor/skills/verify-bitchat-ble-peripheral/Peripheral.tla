------------------------------ MODULE Peripheral ------------------------------
EXTENDS Naturals
CONSTANT RequireReady
VARIABLE p
Init ==
    p = [started |-> FALSE, power |-> FALSE, installed |-> FALSE,
         ready |-> FALSE, advertising |-> FALSE, pending |-> 0]
Configure(old) ==
    IF old.started /\ old.power
    THEN IF old.installed
         THEN [old EXCEPT !.advertising = old.ready \/ ~RequireReady]
         ELSE [old EXCEPT !.installed = TRUE]
    ELSE old
Start ==
    /\ ~p.started
    /\ p' = Configure([p EXCEPT !.started = TRUE])
PowerOn ==
    /\ ~p.power
    /\ p' = Configure([p EXCEPT !.power = TRUE])
PowerOff ==
    /\ p.power
    /\ p' = [p EXCEPT !.power = FALSE, !.ready = FALSE, !.advertising = FALSE]
ServiceAdded ==
    /\ p.started /\ p.power /\ p.installed /\ ~p.ready
    /\ p' = [p EXCEPT !.ready = TRUE, !.advertising = TRUE]
Enqueue ==
    /\ p.started /\ p.installed
    /\ p' = [p EXCEPT !.pending = IF @ < 2 THEN @ + 1 ELSE @]
Deliver ==
    /\ p.started /\ p.installed /\ p.ready /\ p.pending > 0
    /\ p' = [p EXCEPT !.pending = @ - 1]
Backpressure ==
    /\ p.started /\ p.installed /\ p.ready /\ p.pending > 0
    /\ UNCHANGED p
Stop ==
    /\ p.started
    /\ p' = [p EXCEPT !.started = FALSE, !.installed = FALSE, !.ready = FALSE,
                       !.advertising = FALSE, !.pending = 0]
Idle == UNCHANGED p
Next == Start \/ PowerOn \/ PowerOff \/ ServiceAdded \/ Enqueue \/ Deliver \/ Backpressure \/ Stop \/ Idle
QueueBound == p.pending \in 0..2
StoppedClearsOutput == ~p.started => ~p.advertising /\ p.pending = 0
AdvertisesReadyService == p.advertising => p.ready
=============================================================================
