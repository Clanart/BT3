# Q2055: Solana init_transfer_sol native versus wrapped branch switch

## Question
Can an unprivileged attacker choose inputs to `public Solana `init_transfer_sol` instruction` that make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` classify the asset differently before and after a custody-changing step through handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models.
