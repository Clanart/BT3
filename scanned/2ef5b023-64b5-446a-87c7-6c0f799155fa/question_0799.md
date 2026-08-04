# Q799: try_lock_accounts capitalization drift

## Question
Can an unprivileged attacker reach `try_lock_accounts` by submit transactions via `sendtransaction` or direct tpu quic with duplicated writable/read-only aliases, address lookup tables, and batched conflicting write sets such that lamport deltas can leave capitalization counters inconsistent with the actual account set, breaking the invariant that global capitalization must equal the sum of committed account balances and leading to `Loss of Funds`?

## Target
- File/function: runtime/src/bank.rs::try_lock_accounts
- Entrypoint: submit transactions via `sendTransaction` or direct TPU QUIC
- Attacker controls: duplicated writable/read-only aliases, address lookup tables, and batched conflicting write sets
- Exploit idea: make failed or partial writes skew aggregate lamport accounting
- Invariant to test: global capitalization must equal the sum of committed account balances
- Expected Immunefi impact: Loss of Funds
- Fast validation: compare capitalization counters to reconstructed account sums after late-failing multi-write transactions
