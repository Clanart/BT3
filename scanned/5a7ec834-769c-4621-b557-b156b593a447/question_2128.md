# Q2128: Solana deploy response serialization hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public deploy instruction through `DeployToken::initialize_token_metadata`` with overlong or adversarial token identifiers and make `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` derive the same local seed or salt for two remote assets because of serializes the deploy response that Near uses to bind or trust the Solana-side representation of a remote asset, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
