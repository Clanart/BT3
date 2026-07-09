# Q3186: NEAR resolve_fast_transfer removed fast transfer can be replayed or claimed

## Question
Can an unprivileged attacker use `callback after `send_tokens` in the fast Near path` to force `near/omni-bridge/src/lib.rs::resolve_fast_transfer` to remove fast-transfer state before every dependent effect is final, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id.
