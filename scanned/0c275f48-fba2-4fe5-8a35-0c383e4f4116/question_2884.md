# Q2884: notify_gossip_subscribers roots-to-flush backlog blowup

## Question
Can an unprivileged attacker reach `notify_gossip_subscribers` by trigger in-scope subscriptions and then submit transactions that generate hot notifications with subscription mix, slow consumer behavior, and hot event streams so that attacker-driven write patterns can keep the pending-root backlog large enough to destabilize memory or latency, breaking the invariant that pending roots and flush backlog must stay bounded under valid user workload and leading to `DoS Attacks`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_gossip_subscribers
- Entrypoint: trigger in-scope subscriptions and then submit transactions that generate hot notifications
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: treat backlog growth as the exploit
- Invariant to test: pending roots and flush backlog must stay bounded under valid user workload
- Expected Immunefi impact: DoS Attacks
- Fast validation: drive repeated write bursts across roots and monitor backlog size
