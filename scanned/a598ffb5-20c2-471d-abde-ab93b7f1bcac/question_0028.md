# Q28: getGenesisHash cursor pinning

## Question
Can an unprivileged attacker enter through `getGenesisHash` and supply rpc params, commitment, encoding flags, and boundary account/slot/message inputs so that `get_genesis_hash` hits a path where attacker-chosen slots, signatures, or offsets keep this method walking or re-checking large spans of state that rarely change, breaking the invariant that attacker-provided cursors should not pin repeated deep scans of state and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_genesis_hash
- Entrypoint: JSON-RPC `getGenesisHash` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: RPC params, commitment, encoding flags, and boundary account/slot/message inputs
- Exploit idea: look for cursors that force repeated scans of the same cold region
- Invariant to test: attacker-provided cursors should not pin repeated deep scans of state
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: hammer stable boundary cursors and compare keys visited per request
