# Q2617: accounts_db.add_root queue-drain mismatch

## Question
Can an unprivileged attacker reach `add_root` by submit transactions that maximize write churn near root movement with heavy write churn near root movement so that the queue behind this function drains more slowly than one valid subscription shape can fill it even at realistic rates, breaking the invariant that one valid subscription must not create a persistently negative drain ratio and leading to `RPC DoS/Crash`?

## Target
- File/function: accounts-db/src/accounts_db.rs::add_root
- Entrypoint: submit transactions that maximize write churn near root movement
- Attacker controls: heavy write churn near root movement
- Exploit idea: treat steady-state drain ratio as the invariant
- Invariant to test: one valid subscription must not create a persistently negative drain ratio
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: measure fill/drain ratio for the hottest legal notification source
