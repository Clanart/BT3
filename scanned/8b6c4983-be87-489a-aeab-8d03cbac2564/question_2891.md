# Q2891: notify_gossip_subscribers result-cloning chain

## Question
Can an unprivileged attacker reach `notify_gossip_subscribers` by trigger in-scope subscriptions and then submit transactions that generate hot notifications with subscription mix, slow consumer behavior, and hot event streams so that large notification objects are cloned more than necessary, breaking the invariant that notification emission should avoid redundant cloning of large payloads and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_gossip_subscribers
- Entrypoint: trigger in-scope subscriptions and then submit transactions that generate hot notifications
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: look for repeated clones of the same large object
- Invariant to test: notification emission should avoid redundant cloning of large payloads
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: profile allocations and clone counts while delivering large notifications
