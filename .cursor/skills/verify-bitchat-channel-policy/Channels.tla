------------------------------- MODULE Channels -------------------------------
CONSTANT ClearPrevious
VARIABLES selected, joined, retain, password, listed
Places == {"first", "second"}
Init ==
    /\ selected = {} /\ joined = FALSE /\ retain = FALSE
    /\ password = FALSE /\ listed = FALSE
Select ==
    /\ \E place \in Places : selected' = IF ClearPrevious THEN {place} ELSE selected \cup {place}
    /\ UNCHANGED <<joined, retain, password, listed>>
Unselect ==
    /\ \E place \in Places : selected' = selected \ {place}
    /\ UNCHANGED <<joined, retain, password, listed>>
TopicJoin ==
    /\ joined' = TRUE
    /\ retain' \in {retain, TRUE} /\ password' \in BOOLEAN
    /\ UNCHANGED <<selected, listed>>
TopicLeave == joined' = FALSE /\ UNCHANGED <<selected, retain, password, listed>>
TopicSettings ==
    /\ retain' \in BOOLEAN /\ password' \in BOOLEAN
    /\ UNCHANGED <<selected, joined, listed>>
List == listed' = TRUE /\ UNCHANGED <<selected, joined, retain, password>>
Next == Select \/ Unselect \/ TopicJoin \/ TopicLeave \/ TopicSettings \/ List
SingleSelectedGeohash == ~ (selected = Places)
=============================================================================
