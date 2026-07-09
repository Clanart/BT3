# Q2731: Solana deploy response serialization endianness mismatch forks authenticated bytes

## Question
Can an unprivileged attacker exploit `public deploy instruction through `DeployToken::initialize_token_metadata`` so that `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs` serializes or parses numeric fields in an order that differs from another chain’s implementation, violating `deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`
- Entrypoint: `public deploy instruction through `DeployToken::initialize_token_metadata``
- Attacker controls: remote token id, minted Solana address, capped decimals, and origin decimals
- Exploit idea: Target Borsh helpers and hand-built payload encoders across Rust, Solidity, and Cairo.
- Invariant to test: deploy-response bytes must not let Near bind the wrong mint or trust the wrong decimal relationship
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Cross-generate payloads on every implementation and assert byte-for-byte equality for every field combination that can reach signatures or proofs.
