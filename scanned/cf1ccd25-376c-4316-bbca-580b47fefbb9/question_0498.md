# Q498: NEAR callback gas budgeting burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `public finalization and fast-transfer flows with user-controlled `msg`` with control over message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call` and desynchronize `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring, violating `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` and the adjacent mint, burn, or custody accounting after every branch.
