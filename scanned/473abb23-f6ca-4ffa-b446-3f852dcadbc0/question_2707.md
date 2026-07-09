# Q2707: NEAR wNEAR unwrap path callback-bearing token flow exposes inconsistent intermediate state

## Question
Can an unprivileged attacker exploit a callback-bearing branch in `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` so that `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` exposes intermediate state that a receiver or token contract can act on inconsistently, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof.
