# Q2140: spawn_simple_qos_server rollback dirty state

## Question
Can an unprivileged attacker reach `spawn_simple_qos_server` by submit transactions directly over tpu quic from one client with connection counts, packet pacing, payload sizes, and source-ip churn timing such that a failing transaction can leave dirty cache, balance, or metadata state behind even though execution is reported as failed, breaking the invariant that failed transactions must not leak state changes into later execution or rpc views and leading to `Consensus/Safety Violations`?

## Target
- File/function: streamer/src/quic.rs::spawn_simple_qos_server
- Entrypoint: submit transactions directly over TPU QUIC from one client
- Attacker controls: connection counts, packet pacing, payload sizes, and source-IP churn timing
- Exploit idea: search for post-failure state that survives into later reads or commits
- Invariant to test: failed transactions must not leak state changes into later execution or RPC views
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: force late failures after many writes and diff caches and post-state against a fresh bank reconstruction
