# Q3106: Solana DeployToken initialize_token_metadata native versus wrapped registration confusion at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow through `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` violate `mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset` in the `native versus wrapped registration confusion` attack class because hashes overlong token strings to derive mint seeds, caps decimals, writes Metaplex metadata, and posts a `DeployTokenResponse` back to Near becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata`
- Entrypoint: `public deploy flow through `deploy_token``
- Attacker controls: token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
