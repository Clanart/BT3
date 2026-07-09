# Q3388: Starknet verify_borsh_signature optional-field encoding ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public signature-check path through `deploy_token` and `fin_transfer`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::_verify_borsh_signature` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional-field encoding ambiguity` under hashes Borsh bytes with Keccak, reconstructs an Ethereum-style signature, and checks it against the configured derived address, violating `signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_verify_borsh_signature`
- Entrypoint: `public signature-check path through `deploy_token` and `fin_transfer``
- Attacker controls: serialized Borsh payload bytes, signature `v/r/s`, and configured derived address
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature verification must be domain-separated and malleability-safe so one signature cannot authorize multiple Starknet bridge actions
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
