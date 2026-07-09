# Q471: NEAR wNEAR unwrap path asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` with control over token id chosen through mapping, recipient account, amount, and callback success/failure and desynchronize `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` and the adjacent mint, burn, or custody accounting after every branch.
