# Q3699: NEAR fin_transfer_send_tokens_callback fee recipient can be substituted or reclaimed by attacker at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for Near finalization` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` violate `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches` in the `fee recipient can be substituted or reclaimed by attacker` attack class because either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
