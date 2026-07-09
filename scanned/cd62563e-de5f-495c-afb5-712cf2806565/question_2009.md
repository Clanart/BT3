# Q2009: NEAR add_fast_transfer fast amount-plus-fee check can be bypassed

## Question
Can an unprivileged attacker craft a fast-transfer input to `internal state writer reached from public fast-finalization flows` so that `near/omni-bridge/src/lib.rs::add_fast_transfer` accepts an amount-plus-fee equality that does not correspond to the eventual canonical transfer because of persists relayer-sponsored fast-transfer state with `finalised = false` and reserves storage for later settlement, violating `one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fast_transfer`
- Entrypoint: `internal state writer reached from public fast-finalization flows`
- Attacker controls: fast transfer id, relayer identity, storage payer, recipient, amount, fee, and origin transfer id
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths.
- Invariant to test: one fast-transfer id must never correspond to multiple relayers, multiple recipients, or multiple payable later legs
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token.
