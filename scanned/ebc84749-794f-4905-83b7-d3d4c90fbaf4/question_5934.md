# Q5934: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
In rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the attacker repeats the call in the same block to observe the second, no-op iteration, so that `userInfo[_stakingToken][user].amount` diverges from `_calLpSupply(_stakingToken)`, the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats the call in the same block to observe the second, no-op iteration, call `multiclaim(address[] _stakingTokens)`, and assert `userInfo[_stakingToken][user].amount` equals `_calLpSupply(_stakingToken)` and that no account can withdraw more than it put in.
