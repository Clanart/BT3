# Q4516: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Does `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` let an unprivileged caller exploit that under the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` diverges from `block.timestamp`, the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, have the attacker run `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, then assert the victim's claimable value and the `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` versus `block.timestamp` relation are unchanged by the attacker's transaction.
