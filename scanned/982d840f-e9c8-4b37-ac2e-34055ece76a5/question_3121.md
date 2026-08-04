# Q3121: filter_account_result zero-lamport resurrection

## Question
Can an unprivileged attacker reach `filter_account_result` by use in-scope account subscriptions and hot account churn with account subscription filters, encodings, and hot account streams so that dead or zero-lamport accounts can survive or reappear because cleanup and load paths disagree, breaking the invariant that closed accounts must not resurrect without a valid recreation path and leading to `Loss of Funds`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::filter_account_result
- Entrypoint: use in-scope account subscriptions and hot account churn
- Attacker controls: account subscription filters, encodings, and hot account streams
- Exploit idea: look for stale dead-account visibility after close/recreate churn
- Invariant to test: closed accounts must not resurrect without a valid recreation path
- Expected Immunefi impact: Loss of Funds
- Fast validation: close and recreate the same attacker-controlled account shape repeatedly
