# Q3307: Solana deploy response serialization address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public deploy instruction through `DeployToken::initialize_token_metadata`` so that `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` normalizes two distinct chain-specific addresses into the same local representation, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
