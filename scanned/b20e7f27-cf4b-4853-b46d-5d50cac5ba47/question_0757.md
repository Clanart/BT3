# Q757: Solana DeployToken initialize_token_metadata partial deployment rollback leaves live alias

## Question
Can an unprivileged attacker trigger a partial failure through `public deploy flow through `deploy_token`` such that `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata` leaves behind either a live token without mappings or mappings without a usable token because of hashes overlong token strings to derive mint seeds, caps decimals, writes Metaplex metadata, and posts a `DeployTokenResponse` back to Near, violating `mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs::initialize_token_metadata`
- Entrypoint: `public deploy flow through `deploy_token``
- Attacker controls: token string bytes, name, symbol, decimals, payer funding, and metadata PDA seeds
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed.
- Invariant to test: mint-seed derivation, decimal capping, and metadata writes must produce one canonical wrapped token per signed remote asset
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound.
