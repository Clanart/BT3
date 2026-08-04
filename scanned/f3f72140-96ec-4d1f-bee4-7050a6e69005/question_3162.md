# Q3162: filter_signature_result notification context drift

## Question
Can an unprivileged attacker reach `filter_signature_result` by use in-scope signature subscriptions and many status changes with signature subscription parameters and hot status churn so that watcher or filter state can pair payloads with the wrong slot/root context, breaking the invariant that notification payloads and context must describe the same event/state and leading to `Consensus/Safety Violations`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_signature_result
- Entrypoint: use in-scope signature subscriptions and many status changes
- Attacker controls: signature subscription parameters and hot status churn
- Exploit idea: look for impossible payload/context combinations
- Invariant to test: notification payloads and context must describe the same event/state
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: cross-check delivered notifications against direct state at the same reported slot/root
