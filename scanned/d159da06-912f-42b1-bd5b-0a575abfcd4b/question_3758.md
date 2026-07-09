# Q3758: NEAR Wormhole DeployToken conversion decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public Wormhole deploy-token proof flow` with a token whose remote decimals exceed local limits and make `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>` register a representation that breaks backing assumptions because of Borsh-decodes a Wormhole payload into `DeployTokenMessage` and derives the emitter address from the token-address chain and VAA emitter bytes, violating `one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<DeployTokenMessage>`
- Entrypoint: `public Wormhole deploy-token proof flow`
- Attacker controls: payload bytes inside a validated VAA, token id string, token address chain, decimals, and origin decimals
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: one remote token identity must map to one local deploy-token message with one canonical chain binding and decimal model
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
