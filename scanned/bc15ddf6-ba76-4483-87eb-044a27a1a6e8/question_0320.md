# Q320: getTokenAccountsByDelegate boundary-object blowup

## Question
Can an unprivileged attacker use `getTokenAccountsByDelegate` within the single-client low-rate model and choose filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled such that `get_token_accounts_by_delegate` triggers a path where one legal boundary object such as a dense block, large account set, or verbose notification has outsized cost inside this method, violating the invariant that worst-case legal objects must still stay within safe service budgets and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: use the heaviest legal object the method can surface
- Invariant to test: worst-case legal objects must still stay within safe service budgets
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: pick the densest live object the method can return and measure peak heap and latency
