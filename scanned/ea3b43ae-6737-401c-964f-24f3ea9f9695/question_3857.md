# Q3857: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
rewards/MasterMagpie.sol: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. With the full _stakingTokens array, including duplicates and unregistered addresses under attacker control and the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, can an unprivileged caller sequence `multiclaim(address[] _stakingTokens)` so that `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` no longer reconcile, violating the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, then assert `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` end identical in both runs.
