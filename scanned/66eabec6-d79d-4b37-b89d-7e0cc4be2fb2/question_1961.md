# Q1961: NEAR fin_transfer_send_tokens_callback unlock or relock asymmetry at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for Near finalization` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` violate `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches` in the `unlock or relock asymmetry` attack class because either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
