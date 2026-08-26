# Q3951: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, is there an unprivileged sequence of `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` that leaves `unClaimedMgp[_stakingToken][user]` unreconciled with `userInfo[_stakingToken][user].rewardDebt`, violates the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, have the attacker run `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, then assert the victim's claimable value and the `unClaimedMgp[_stakingToken][user]` versus `userInfo[_stakingToken][user].rewardDebt` relation are unchanged by the attacker's transaction.
