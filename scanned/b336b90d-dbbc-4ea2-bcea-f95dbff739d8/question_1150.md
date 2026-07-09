# Q1150: NEAR fin_transfer_send_tokens_callback asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `callback after sending tokens for Near finalization` with control over transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because either burns minted bridge tokens and reverts lock actions on failure or mints/transfers fee assets on success, violating `success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback`
- Entrypoint: `callback after sending tokens for Near finalization`
- Attacker controls: transfer message, fee recipient, whether `msg` was empty, storage owner, and recorded lock actions
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: success and failure branches must be perfectly complementary so an attacker cannot keep principal, fees, or unlocked liquidity from both branches
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer_send_tokens_callback` and the adjacent lock and unlock accounting after every branch.
