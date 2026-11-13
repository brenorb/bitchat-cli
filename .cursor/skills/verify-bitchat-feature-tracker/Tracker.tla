------------------------------- MODULE Tracker --------------------------------
EXTENDS Naturals
CONSTANTS DynamicSummary, Grow
VARIABLES phase, rows, validation, summary
Init == phase = "populate" /\ rows = 0 /\ validation = 0 /\ summary = 0
Append ==
    /\ phase = "populate" /\ rows < (IF Grow THEN 3 ELSE 2)
    /\ rows' = rows + 1
    /\ UNCHANGED <<phase, validation, summary>>
Rules ==
    /\ phase = "populate" /\ rows >= 2
    /\ phase' = "summary" /\ validation' = rows
    /\ UNCHANGED <<rows, summary>>
Summarize ==
    /\ phase = "summary"
    /\ phase' = "save"
    /\ summary' = IF DynamicSummary THEN rows ELSE 2
    /\ UNCHANGED <<rows, validation>>
Save == phase = "save" /\ phase' = "saved" /\ UNCHANGED <<rows, validation, summary>>
Done == phase = "saved" /\ UNCHANGED <<phase, rows, validation, summary>>
Next == Append \/ Rules \/ Summarize \/ Save \/ Done
SummaryCoversRows == phase = "saved" => summary = rows /\ validation = rows
=============================================================================
