# Q2718: NEAR fin_transfer_send_tokens_callback final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `callback after sending tokens for Near finalization` so that `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
