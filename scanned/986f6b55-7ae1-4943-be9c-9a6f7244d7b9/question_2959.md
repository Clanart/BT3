# Q2959: notify_signatures_received roots-to-flush backlog blowup

## Question
Can an unprivileged attacker reach `notify_signatures_received` by subscribe to signatures and then submit many status-changing transactions with signature subscriptions, duplicate-signature churn, and slow consumer behavior so that attacker-driven write patterns can keep the pending-root backlog large enough to destabilize memory or latency, breaking the invariant that pending roots and flush backlog must stay bounded under valid user workload and leading to `DoS Attacks`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_signatures_received
- Entrypoint: subscribe to signatures and then submit many status-changing transactions
- Attacker controls: signature subscriptions, duplicate-signature churn, and slow consumer behavior
- Exploit idea: treat backlog growth as the exploit
- Invariant to test: pending roots and flush backlog must stay bounded under valid user workload
- Expected Immunefi impact: DoS Attacks
- Fast validation: drive repeated write bursts across roots and monitor backlog size
