# Q806: try_lock_accounts writeback ordering

## Question
Can an unprivileged attacker reach `try_lock_accounts` by submit transactions via `sendtransaction` or direct tpu quic with duplicated writable/read-only aliases, address lookup tables, and batched conflicting write sets such that writes can land in a different order than the logic assumed when computing fees, locks, or state deltas, breaking the invariant that writeback ordering must not invalidate earlier safety decisions and leading to `Consensus/Safety Violations`?

## Target
- File/function: runtime/src/bank.rs::try_lock_accounts
- Entrypoint: submit transactions via `sendTransaction` or direct TPU QUIC
- Attacker controls: duplicated writable/read-only aliases, address lookup tables, and batched conflicting write sets
- Exploit idea: search for ordering dependencies that break under batching or CPI
- Invariant to test: writeback ordering must not invalidate earlier safety decisions
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: trace write order and derived counters under multi-instruction, multi-CPI transactions
