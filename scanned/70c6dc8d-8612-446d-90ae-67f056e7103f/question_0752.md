# Q752: Solana init_transfer burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public Solana `init_transfer` instruction` to force `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
