# Q834: Solana deploy response serialization decimal cap creates wrong economic model

## Question
Can an unprivileged attacker reach `public deploy instruction through `DeployToken::initialize_token_metadata`` with a token whose remote decimals exceed local limits and make `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` register a representation that breaks backing assumptions because of serializes the deploy response that Near uses to bind or trust the Solana-side representation of a remote asset, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset.
