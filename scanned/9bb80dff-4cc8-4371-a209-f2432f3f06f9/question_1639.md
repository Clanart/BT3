# Q1639: NEAR fin_transfer_send_tokens_callback unlock or relock asymmetry via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after sending tokens for Near finalization` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `unlock or relock asymmetry` under either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
