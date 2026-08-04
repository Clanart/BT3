# Q406: simulateTransaction preflight saturation

## Question
Can an unprivileged attacker enter through `simulateTransaction` and supply serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags so that `simulate_transaction` hits a path where preflight or simulation work can be made much more expensive than a normal submission by attacker-chosen transaction structure, breaking the invariant that a single low-rate client must not monopolize rpc execution threads with one transaction shape and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::simulate_transaction
- Entrypoint: JSON-RPC `simulateTransaction` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: serialized transaction bytes, versioned messages, address lookup tables, compute-budget instructions, account metas, signatures, and preflight flags
- Exploit idea: use legal transaction features to amplify dry-run or preflight cost
- Invariant to test: a single low-rate client must not monopolize RPC execution threads with one transaction shape
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: replay worst-case legal transactions and compare executor latency against a simple transfer
