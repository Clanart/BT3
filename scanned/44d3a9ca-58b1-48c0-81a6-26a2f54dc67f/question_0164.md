# Q164: Solana deploy response serialization canonical token identity collision

## Question
Can an unprivileged attacker reach `public deploy instruction through `DeployToken::initialize_token_metadata`` with a valid-looking remote asset identity and make `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` map it onto an existing local token because of serializes the deploy response that Near uses to bind or trust the Solana-side representation of a remote asset, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row.
