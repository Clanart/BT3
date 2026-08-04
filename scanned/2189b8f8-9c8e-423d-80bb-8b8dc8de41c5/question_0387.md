# Q387: sendTransaction log/return-data blowup

## Question
Can an unprivileged attacker enter through `sendTransaction` and supply serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags so that `send_transaction` hits a path where attacker-controlled execution side effects like logs, return data, or inner-instruction detail can retain far more memory than the request size suggests, breaking the invariant that dry-run execution artifacts must stay within bounded per-request memory budgets and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::send_transaction
- Entrypoint: JSON-RPC `sendTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags
- Exploit idea: exploit execution artifacts rather than raw byte size
- Invariant to test: dry-run execution artifacts must stay within bounded per-request memory budgets
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: build a legal transaction that maximizes logs/return data
