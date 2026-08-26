# Q3841: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
In rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an unprivileged attacker reach this through `multiclaim(address[] _stakingTokens)` while the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, and drive `userInfo[_stakingToken][user].rewardDebt` out of agreement with `tokenToPoolInfo[_stakingToken].accMGPPerShare` - breaking the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, snapshot `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare`, run the attacker's `multiclaim(address[] _stakingTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
