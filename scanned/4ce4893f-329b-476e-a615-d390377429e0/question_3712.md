# Q3712: Solana deploy response serialization address alias collapses distinct bridge subjects at boundary values

## Question
Can an unprivileged attacker trigger `public deploy instruction through `DeployToken::initialize_token_metadata`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` violate `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship` in the `address alias collapses distinct bridge subjects` attack class because serializes the deploy response that Near uses to bind or trust the Solana-side representation of a remote asset becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
