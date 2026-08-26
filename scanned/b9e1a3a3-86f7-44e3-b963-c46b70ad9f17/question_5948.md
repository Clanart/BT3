# Q5948: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol - _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an unprivileged attacker controlling both outer and inner arrays, so every reward-token address and its order, under the attacker repeats the call in the same block to observe the second, no-op iteration, exploit this through `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to break the reconciliation between `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` and the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats the call in the same block to observe the second, no-op iteration, call `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, and assert `userInfo[_stakingToken][user].available` equals `userInfo[_stakingToken][user].amount` and that no account can withdraw more than it put in.
