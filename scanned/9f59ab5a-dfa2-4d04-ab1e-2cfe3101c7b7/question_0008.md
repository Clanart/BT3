# Q8: NEAR fast_fin_transfer dispatcher origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through ``ft_on_transfer` branch for fast finalization` with control over token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount and make `near/omni-bridge/src/lib.rs::fast_fin_transfer` advance or reuse bridge nonces inconsistently with requires a trusted relayer, denormalizes amount and fee for the origin token, checks whether the referenced transfer is already finalised, and either pays a Near recipient immediately or emits a new transfer to another chain, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fast_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for fast finalization`
- Attacker controls: token id, amount, signer identity as relayer, transfer id, recipient, fee, message, and optional storage deposit amount
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: a fast path must never let relayers front-load value for an event that can later settle differently, twice, or with a different fee/recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
