# Q153: Solana signed-payload serialization recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `public Solana deploy/init/finalize instructions` and make `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` encode or parse it differently than downstream chains expect via serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
