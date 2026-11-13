-------------------------------- MODULE Input --------------------------------
EXTENDS Naturals
CONSTANT CheckSource
VARIABLE s
Init ==
    \E kind \in {"password file", "password pipe", "message pipe", "chat"},
       valid, tty \in BOOLEAN, sourceCount \in 0..2 :
        s = [phase |-> "validate", kind |-> kind, valid |-> valid,
             tty |-> tty, sourceCount |-> sourceCount]
ValidInput == s.valid /\ s.sourceCount <= 1 /\
    (CASE s.kind = "chat" -> s.tty
       [] s.kind \in {"password pipe", "message pipe"} -> ~s.tty
       [] OTHER -> TRUE)
Check ==
    /\ s.phase = "validate"
    /\ s' = [s EXCEPT !.phase =
        IF ValidInput \/ (~CheckSource /\ s.kind = "password file" /\ s.sourceCount <= 1)
        THEN "action" ELSE "failed"]
Done == s.phase \in {"action", "failed"} /\ UNCHANGED s
Next == Check \/ Done
ActionRequiresValidInput == s.phase = "action" => ValidInput
=============================================================================
