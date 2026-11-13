------------------------------ MODULE Package ------------------------------
EXTENDS Naturals
CONSTANT RequireWheel
VARIABLES tagged, phase, attempted
vars == <<tagged, phase, attempted>>

Init == /\ tagged \in BOOLEAN /\ phase = "start" /\ attempted = FALSE
Build ==
  /\ tagged /\ phase = "start"
  /\ phase' \in {"sdist", "failed"}
  /\ UNCHANGED <<tagged, attempted>>
Wheel ==
  /\ phase = "sdist"
  /\ phase' \in {"built", "failed"}
  /\ UNCHANGED <<tagged, attempted>>
Publish ==
  /\ tagged /\ ~attempted
  /\ phase = "built" \/ (~RequireWheel /\ phase = "sdist")
  /\ attempted' = TRUE
  /\ UNCHANGED <<tagged, phase>>

Next == Build \/ Wheel \/ Publish \/ UNCHANGED vars
BuildBeforePublish == attempted => tagged /\ phase = "built"
=============================================================================
