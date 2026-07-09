# Q3274: Solana fake-bridged-token branch check native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `public Solana init/finalize instructions when no vault exists` and make `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs` treat a wrapped asset as native or a native asset as wrapped because of treats a mint as bridged when the vault PDA does not exist and only checks that the mint authority is the bridge authority, violating `bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs`
- Entrypoint: `public Solana init/finalize instructions when no vault exists`
- Attacker controls: mint authority, authority PDA, existence of the vault account, and mint registration timing
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
