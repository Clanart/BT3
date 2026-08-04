# Q346: getRecentPerformanceSamples response amplification

## Question
Can an unprivileged attacker enter through `getRecentPerformanceSamples` and supply rpc params, commitment, encoding flags, and boundary account/slot/message inputs so that `get_recent_performance_samples` hits a path where a compact request materializes or clones a much larger response object graph than the caller paid for, breaking the invariant that response size and retained heap should stay proportional to the requested work and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_recent_performance_samples
- Entrypoint: JSON-RPC `getRecentPerformanceSamples` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: RPC params, commitment, encoding flags, and boundary account/slot/message inputs
- Exploit idea: measure whether response construction dominates the underlying lookup
- Invariant to test: response size and retained heap should stay proportional to the requested work
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: compare request size to peak heap and response size for worst-case legal params
