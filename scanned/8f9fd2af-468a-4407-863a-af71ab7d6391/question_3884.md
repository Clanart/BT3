# Q3884: NEAR Wormhole DeployToken conversion decimal cap creates wrong economic model via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Wormhole deploy-token proof flow` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` ends up accepting two inconsistent interpretations of the same economic event specifically around `decimal cap creates wrong economic model` under Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
