# Q1800: NEAR fin_transfer_send_tokens_callback unlock or relock asymmetry through cross-module drift

## Question
Can an unprivileged attacker use `callback after sending tokens for Near finalization` with control over transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `unlock or relock asymmetry` attack class because either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` and the adjacent lock and unlock accounting after every branch.
