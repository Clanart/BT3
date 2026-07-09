# Q1291: Solana fake-bridged-token branch check native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public Solana init/finalize instructions when no vault exists` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs` violate `bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets` in the `native versus wrapped branch switch` attack class because treats a mint as bridged when the vault PDA does not exist and only checks that the mint authority is the bridge authority becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs`
- Entrypoint: `public Solana init/finalize instructions when no vault exists`
- Attacker controls: mint authority, authority PDA, existence of the vault account, and mint registration timing
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
