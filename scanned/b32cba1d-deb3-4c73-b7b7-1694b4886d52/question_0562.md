# Q562: isBlockhashValid stale-cache inconsistency

## Question
Can an unprivileged attacker enter through `isBlockhashValid` and supply rpc params, commitment, encoding flags, and boundary account/slot/message inputs so that `is_blockhash_valid` hits a path where stale cache content can outlive the bank state this method is supposed to expose, breaking the invariant that caches must not let clients observe impossible combinations of slot, hash, balance, or account data and leading to `Consensus/Safety Violations`?

## Target
- File/function: rpc/src/rpc.rs::is_blockhash_valid
- Entrypoint: JSON-RPC `isBlockhashValid` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: RPC params, commitment, encoding flags, and boundary account/slot/message inputs
- Exploit idea: make a stale cached object survive long enough to be returned as current
- Invariant to test: caches must not let clients observe impossible combinations of slot, hash, balance, or account data
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: race state changes against repeated reads and diff against a direct bank view
