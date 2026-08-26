# Q3966: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
rewards/MasterMagpie.sol: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, is there an unprivileged sequence of `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` that leaves `_calLpSupply(_stakingToken)` unreconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violates the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, snapshot `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)`, run the attacker's `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
