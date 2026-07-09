# Q483: NEAR fin_transfer_send_tokens_callback recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `callback after sending tokens for Near finalization` with control over transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` and the adjacent lock and unlock accounting after every branch.
