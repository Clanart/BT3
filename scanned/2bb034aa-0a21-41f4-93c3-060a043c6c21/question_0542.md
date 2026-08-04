# Q542: getFirstAvailableBlock range explosion

## Question
Can an unprivileged attacker enter through `getFirstAvailableBlock` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_first_available_block` hits a path where the legal upper bound or default limit may still be too high when the returned objects are attacker-selected worst cases, breaking the invariant that worst-case legal ranges must stay safe for one-client rpc servicing and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_first_available_block
- Entrypoint: JSON-RPC `getFirstAvailableBlock` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: use allowed limits but maximize the weight of each object
- Invariant to test: worst-case legal ranges must stay safe for one-client RPC servicing
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: select the densest in-scope range and compare request cost to smaller windows
