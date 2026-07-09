# Q3646: Solana init_transfer callback refund creates value gap at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `init_transfer` instruction` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` violate `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge` in the `callback refund creates value gap` attack class because charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
