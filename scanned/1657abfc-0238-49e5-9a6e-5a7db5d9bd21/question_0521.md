# Q521: getSignaturesForAddress cursor regression

## Question
Can an unprivileged attacker enter through `getSignaturesForAddress` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_signatures_for_address` hits a path where a legal cursor or pagination combination causes the method to revisit already-scanned history rather than advancing monotonically, breaking the invariant that pagination should advance or fail quickly; it should not repeatedly restart deep scans and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_signatures_for_address
- Entrypoint: JSON-RPC `getSignaturesForAddress` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: pin the iterator near a boundary and see if low-rate requests rescan the same region
- Invariant to test: pagination should advance or fail quickly; it should not repeatedly restart deep scans
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: issue repeated paginated requests with stable boundary cursors
