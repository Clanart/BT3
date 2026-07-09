# Q3456: NEAR resolve_fast_transfer removed fast transfer can be replayed or claimed through cross-module drift

## Question
Can an unprivileged attacker use `callback after `send_tokens` in the fast Near path` with control over token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount and desynchronize `near/omni-bridge/src/lib.rs::resolve_fast_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `removed fast transfer can be replayed or claimed` attack class because burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Look for paths that remove state on refund or fee claim while another leg still depends on it for replay protection or storage refund. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force early removal and assert that no subsequent proof, claim, or callback can recreate or profit from the same fast-transfer id. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::resolve_fast_transfer` and the adjacent mint, burn, or custody accounting after every branch.
