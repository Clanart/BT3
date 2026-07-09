# Q3783: Solana DeployToken initialize_token_metadata low-half deploy salt aliases another token id

## Question
Can an unprivileged attacker reach `public deploy flow through `deploy_token`` and make `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` deploy or reference another token’s address because the contract address salt uses only part of a larger hash, violating `mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata`
- Entrypoint: `public deploy flow through `deploy_token``
- Attacker controls: token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt.
- Invariant to test: mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids.
