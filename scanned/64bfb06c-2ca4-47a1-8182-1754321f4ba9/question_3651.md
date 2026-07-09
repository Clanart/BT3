# Q3651: Solana DeployToken initialize_token_metadata fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow through `deploy_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` violate `mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset` in the `fake bridge-controlled token accepted as canonical` attack class because hashes overlong token strings to derive mint seeds, caps decimals, writes Metaplex metadata, and posts a `DeployTokenResponse` back to Near becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata`
- Entrypoint: `public deploy flow through `deploy_token``
- Attacker controls: token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
