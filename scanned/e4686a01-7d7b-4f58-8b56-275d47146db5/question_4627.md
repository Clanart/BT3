# Q4627: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Does `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` let an unprivileged caller exploit that under the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence atomically under the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, asserting at the end that `IBaseRewardPool(rewarder).balanceOf(user)` still equals `IBaseRewardPool(rewarder).totalStaked()` and the PoC's balance delta is non-positive.
