# Q559: isBlockhashValid boundary decode path

## Question
Can an unprivileged attacker enter through `isBlockhashValid` and supply rpc params, commitment, encoding flags, and boundary account/slot/message inputs so that `is_blockhash_valid` hits a path where deserializable but adversarial boundary inputs force deep parsing or expensive validation before rejection, breaking the invariant that malformed or boundary input should fail quickly without traversing expensive downstream code and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::is_blockhash_valid
- Entrypoint: JSON-RPC `isBlockhashValid` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: RPC params, commitment, encoding flags, and boundary account/slot/message inputs
- Exploit idea: stress every public field that this method decodes
- Invariant to test: malformed or boundary input should fail quickly without traversing expensive downstream code
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: fuzz only the exposed params and record reject latency
