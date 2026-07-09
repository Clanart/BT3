# Q1990: NEAR resolve_fast_transfer fast path can pay before canonical parameters are locked

## Question
Can an unprivileged attacker use `callback after `send_tokens` in the fast Near path` to make `near/omni-bridge/src/lib.rs::resolve_fast_transfer` release a fast-transfer payout before the canonical transfer parameters are irreversibly fixed, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement.
