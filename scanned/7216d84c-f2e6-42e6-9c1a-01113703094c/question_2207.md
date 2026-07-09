# Q2207: Solana init_transfer_sol native versus wrapped branch switch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `init_transfer_sol` instruction` and then replay or reorder the later settlement leg on another chain so that `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped branch switch` under handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
