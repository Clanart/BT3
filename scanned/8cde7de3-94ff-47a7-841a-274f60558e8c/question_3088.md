# Q3088: notify_watchers slow-consumer retention

## Question
Can an unprivileged attacker reach `notify_watchers` by trigger many hot notifications from one subscription shape with subscription mix, slow consumer behavior, and hot event streams so that one slow subscriber can make state created around this function accumulate without bound, breaking the invariant that one slow subscriber must not create unbounded retained notification state and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc_subscriptions.rs::notify_watchers
- Entrypoint: trigger many hot notifications from one subscription shape
- Attacker controls: subscription mix, slow consumer behavior, and hot event streams
- Exploit idea: treat queue retention as the bug class
- Invariant to test: one slow subscriber must not create unbounded retained notification state
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: stop reading from one websocket and monitor retained notification memory
