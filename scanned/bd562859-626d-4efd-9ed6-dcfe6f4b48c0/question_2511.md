# Q2511: Solana init_transfer_sol native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `init_transfer_sol` instruction` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` violate `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana` in the `native versus wrapped branch switch` attack class because handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
