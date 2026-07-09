# Q122: Solana fake-bridged-token branch check burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public Solana init/finalize instructions when no vault exists` to force `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via treats a mint as bridged when the vault PDA does not exist and only checks that the mint authority is the bridge authority, violating `bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs and init_transfer.rs`
- Entrypoint: `public Solana init/finalize instructions when no vault exists`
- Attacker controls: mint authority, authority PDA, existence of the vault account, and mint registration timing
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: bridged/native branch detection must not let an attacker swap a fake bridge-controlled mint into a path intended only for canonical wrapped assets
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
