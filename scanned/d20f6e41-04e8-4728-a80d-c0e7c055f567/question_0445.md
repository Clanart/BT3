# Q445: getBlock signature-slot ambiguity

## Question
Can an unprivileged attacker enter through `getBlock` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_block` hits a path where boundary signatures or slots may map to multiple internal states and the method could pick the wrong one, breaking the invariant that signature and slot mapping must be deterministic and consistent and leading to `Consensus/Safety Violations`?

## Target
- File/function: rpc/src/rpc.rs::get_block
- Entrypoint: JSON-RPC `getBlock` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: exercise signatures around pruning and slot boundaries
- Invariant to test: signature and slot mapping must be deterministic and consistent
- Expected Immunefi impact: Consensus/Safety Violations
- Fast validation: replay queries around boundary signatures and compare resolved identity
