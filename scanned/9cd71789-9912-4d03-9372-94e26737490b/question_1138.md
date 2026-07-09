# Q1138: NEAR wNEAR unwrap path delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` with control over token id chosen through mapping, recipient account, amount, and callback success/failure and desynchronize `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` and the adjacent mint, burn, or custody accounting after every branch.
