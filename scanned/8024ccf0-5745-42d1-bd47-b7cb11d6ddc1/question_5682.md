# Q5682: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. With the full _stakingTokens array, including duplicates and unregistered addresses under attacker control and the contract is paused so only emergencyWithdraw is reachable, can an unprivileged caller sequence `multiclaim(address[] _stakingTokens)` so that `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` no longer reconcile, violating the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract is paused so only emergencyWithdraw is reachable, have the attacker run `multiclaim(address[] _stakingTokens)`, then assert the victim's claimable value and the `totalAllocPoint` versus `tokenToPoolInfo[_stakingToken].allocPoint` relation are unchanged by the attacker's transaction.
