# Q3017: notify_roots queue-drain mismatch

## Question
Can an unprivileged attacker reach `notify_roots` by subscribe to roots and then drive hot slot/root movement with root subscriptions, slow consumer behavior, and hot root movement so that the queue behind this function drains more slowly than one valid subscription shape can fill it even at realistic rates, breaking the invariant that one valid subscription must not create a persistently negative drain ratio and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_roots
- Entrypoint: subscribe to roots and then drive hot slot/root movement
- Attacker controls: root subscriptions, slow consumer behavior, and hot root movement
- Exploit idea: treat steady-state drain ratio as the invariant
- Invariant to test: one valid subscription must not create a persistently negative drain ratio
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: measure fill/drain ratio for the hottest legal notification source
