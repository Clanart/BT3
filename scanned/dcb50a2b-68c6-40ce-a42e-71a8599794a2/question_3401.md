# Q3401: Starknet ETH-signature domain optional-field encoding ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet deploy/finalize entrypoints` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional-field encoding ambiguity` under recovers an Ethereum-style signer over raw Keccak of Borsh bytes rather than a structured Starknet domain separator, violating `signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token and fin_transfer signature path`
- Entrypoint: `public Starknet deploy/finalize entrypoints`
- Attacker controls: payload bytes, chain id fields embedded in payload, and signature `v/r/s` values
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature bytes must stay uniquely bound to the intended Starknet action, chain context, and payload kind and must reject malleable variants
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
