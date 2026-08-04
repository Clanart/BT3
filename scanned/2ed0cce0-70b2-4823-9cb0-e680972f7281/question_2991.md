# Q2991: notify_vote result-cloning chain

## Question
Can an unprivileged attacker reach `notify_vote` by subscribe to votes and then drive hot transaction flow that affects vote visibility with vote subscriptions, slow consumer behavior, and hot notification streams so that large notification objects are cloned more than necessary, breaking the invariant that notification emission should avoid redundant cloning of large payloads and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_vote
- Entrypoint: subscribe to votes and then drive hot transaction flow that affects vote visibility
- Attacker controls: vote subscriptions, slow consumer behavior, and hot notification streams
- Exploit idea: look for repeated clones of the same large object
- Invariant to test: notification emission should avoid redundant cloning of large payloads
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: profile allocations and clone counts while delivering large notifications
