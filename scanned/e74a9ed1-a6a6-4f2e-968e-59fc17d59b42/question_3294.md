# Q3294: NEAR fin_transfer_send_tokens_callback fee recipient can be substituted or reclaimed by attacker

## Question
Can an unprivileged attacker use `callback after sending tokens for Near finalization` to make `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` route a legitimate fee to the wrong account because of either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee.
