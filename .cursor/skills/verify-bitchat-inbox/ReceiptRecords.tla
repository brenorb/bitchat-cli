---------------------------- MODULE ReceiptRecords ----------------------------
CONSTANT DistinguishKinds
VARIABLES pending, disk
Receipts == {"delivered", "read"}
Init == pending = Receipts /\ disk = {}
Append ==
    \E receipt \in pending :
        /\ pending' = pending \ {receipt}
        /\ disk' = IF DistinguishKinds \/ disk = {} THEN disk \cup {receipt} ELSE disk
Done == pending = {} /\ UNCHANGED <<pending, disk>>
Next == Append \/ Done
AllDistinctReceiptsPersist == Receipts \ pending \subseteq disk
=============================================================================
