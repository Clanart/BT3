# Q2890: NEAR fast_fin_transfer dispatcher native versus wrapped branch switch through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for fast finalization` with control over token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount and desynchronize `near/omni-bridge/src/lib.rs::fast_fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped branch switch` attack class because requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fast_fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
