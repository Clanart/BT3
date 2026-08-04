# Q3217: filter_logs_results queue-drain mismatch

## Question
Can an unprivileged attacker reach `filter_logs_results` by use in-scope logs subscriptions with legal filters with logs filters, encodings, and log-heavy transactions so that the queue behind this function drains more slowly than one valid subscription shape can fill it even at realistic rates, breaking the invariant that one valid subscription must not create a persistently negative drain ratio and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_logs_results
- Entrypoint: use in-scope logs subscriptions with legal filters
- Attacker controls: logs filters, encodings, and log-heavy transactions
- Exploit idea: treat steady-state drain ratio as the invariant
- Invariant to test: one valid subscription must not create a persistently negative drain ratio
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: measure fill/drain ratio for the hottest legal notification source
