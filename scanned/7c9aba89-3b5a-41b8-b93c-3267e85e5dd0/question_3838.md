# Q3838: Solana deploy response serialization truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public deploy instruction through `DeployToken::initialize_token_metadata`` and make `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
