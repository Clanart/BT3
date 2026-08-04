# Q520: getSignaturesForAddress encoding-path blowup

## Question
Can an unprivileged attacker enter through `getSignaturesForAddress` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_signatures_for_address` hits a path where historical objects become far more expensive when rendered in attacker-selected encodings or detail levels, breaking the invariant that historical encoding choices must stay within bounded cpu and memory budgets and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_signatures_for_address
- Entrypoint: JSON-RPC `getSignaturesForAddress` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: look for transaction detail and encoding combinations that explode cost
- Invariant to test: historical encoding choices must stay within bounded CPU and memory budgets
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: compare the same ledger object across legal encodings and detail levels
