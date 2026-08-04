# Q1227: check_reserved_keys queue fairness break

## Question
Can an unprivileged attacker reach `check_reserved_keys` by submit transactions via `sendtransaction` or direct tpu quic with reserved-looking pubkeys, duplicated account metas, and versioned message layouts such that attacker-chosen transactions make this function occupy shared scheduling resources long enough to starve cheaper work, breaking the invariant that one heavy transaction shape must not monopolize shared scheduling resources and leading to `Liveness / Loss of Availability`?

## Target
- File/function: runtime/src/bank.rs::check_reserved_keys
- Entrypoint: submit transactions via `sendTransaction` or direct TPU QUIC
- Attacker controls: reserved-looking pubkeys, duplicated account metas, and versioned message layouts
- Exploit idea: measure unfair occupancy rather than raw throughput
- Invariant to test: one heavy transaction shape must not monopolize shared scheduling resources
- Expected Immunefi impact: Liveness / Loss of Availability
- Fast validation: replay one heavy shape alongside cheap transfers and compare scheduling latency
