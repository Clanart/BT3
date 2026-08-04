# Q2878: notify_gossip_subscribers flush-path service pinning

## Question
Can an unprivileged attacker reach `notify_gossip_subscribers` by trigger in-scope subscriptions and then submit transactions that generate hot notifications with subscription mix, slow consumer behavior, and hot event streams so that a legal transaction/read pattern can force this path into a flush-heavy mode that blocks unrelated work, breaking the invariant that background flush work should not let one attacker pattern monopolize service resources and leading to `DoS Attacks`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_gossip_subscribers
- Entrypoint: trigger in-scope subscriptions and then submit transactions that generate hot notifications
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: treat flush pressure as the resource
- Invariant to test: background flush work should not let one attacker pattern monopolize service resources
- Expected Immunefi impact: DoS Attacks
- Fast validation: run churn-heavy writes plus immediate reads and measure latency inflation
