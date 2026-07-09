# Q3241: Solana init_transfer callback refund creates value gap

## Question
Can an unprivileged attacker cause the callback resolution behind `public Solana `init_transfer` instruction` to refund tokens, keep a pending claim, or skip a compensating burn in `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` because of charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer.
