# Q2161: NEAR add_fast_transfer fast amount-plus-fee check can be bypassed via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal state writer reached from public fast-finalization flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::add_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast amount-plus-fee check can be bypassed` under persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
