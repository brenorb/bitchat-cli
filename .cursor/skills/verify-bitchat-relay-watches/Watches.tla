------------------------------- MODULE Watches -------------------------------
CONSTANT RemoveObsolete
VARIABLES desired, installed, phase
Keys == {"first subscription", "second subscription"}
Init == desired = {} /\ installed = {} /\ phase = "ready"
Configure ==
    /\ phase = "ready"
    /\ desired' \in SUBSET Keys
    /\ phase' = "reconcile"
    /\ UNCHANGED installed
Reconcile ==
    /\ phase = "reconcile"
    /\ installed' = IF RemoveObsolete THEN desired ELSE installed \cup desired
    /\ phase' = "ready"
    /\ UNCHANGED desired
Stop ==
    /\ phase = "ready"
    /\ phase' = "stopped" /\ desired' = {} /\ installed' = {}
Done == phase = "stopped" /\ UNCHANGED <<phase, desired, installed>>
Next == Configure \/ Reconcile \/ Stop \/ Done
TasksMatchWatches == phase # "reconcile" => installed = desired
=============================================================================
