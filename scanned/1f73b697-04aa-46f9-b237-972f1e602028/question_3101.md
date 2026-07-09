# Q3101: Solana init_transfer native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `init_transfer` instruction` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` violate `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge` in the `native versus wrapped branch switch` attack class because charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
