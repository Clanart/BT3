# Q3159: filter_signature_result roots-to-flush backlog blowup

## Question
Can an unprivileged attacker reach `filter_signature_result` by use in-scope signature subscriptions and many status changes with signature subscription parameters and hot status churn so that attacker-driven write patterns can keep the pending-root backlog large enough to destabilize memory or latency, breaking the invariant that pending roots and flush backlog must stay bounded under valid user workload and leading to `DoS Attacks`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_signature_result
- Entrypoint: use in-scope signature subscriptions and many status changes
- Attacker controls: signature subscription parameters and hot status churn
- Exploit idea: treat backlog growth as the exploit
- Invariant to test: pending roots and flush backlog must stay bounded under valid user workload
- Expected Immunefi impact: DoS Attacks
- Fast validation: drive repeated write bursts across roots and monitor backlog size
