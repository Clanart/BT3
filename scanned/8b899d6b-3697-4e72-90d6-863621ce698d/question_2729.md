# Q2729: NEAR callback gas budgeting asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `public finalization and fast-transfer flows with user-controlled `msg`` to make `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` release value through a more favorable branch than the source event actually authorized because of computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring, violating `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
