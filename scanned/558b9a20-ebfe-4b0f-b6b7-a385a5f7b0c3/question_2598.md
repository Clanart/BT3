# Q2598: NEAR resolve_fast_transfer relayer substitution changes economic recipient

## Question
Can an unprivileged attacker exploit `callback after `send_tokens` in the fast Near path` so that `near/omni-bridge/src/lib.rs::resolve_fast_transfer` redirects principal or fee to a relayer under conditions that do not match the original user transfer, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout.
