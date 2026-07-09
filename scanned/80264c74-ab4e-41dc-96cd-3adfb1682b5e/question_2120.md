# Q2120: Solana signed-payload serialization one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public Solana deploy/init/finalize instructions` and make `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` both release local value and create a second valid outbound bridge obligation via serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
