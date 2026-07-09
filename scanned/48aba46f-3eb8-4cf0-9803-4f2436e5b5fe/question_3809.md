# Q3809: Solana fake-bridged-token branch check fake bridge-controlled token accepted as canonical

## Question
Can an unprivileged attacker use `public Solana init/finalize instructions when no vault exists` to register or settle against a token that only looks bridge-controlled because `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs` relies on treats a mint as bridged when the vault PDA does not exist and only checks that the mint authority is the bridge authority, violating `bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs`
- Entrypoint: `public Solana init/finalize instructions when no vault exists`
- Attacker controls: mint authority, authority PDA, existence of the vault account, and mint registration timing
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity.
- Invariant to test: bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset.
