# Q5556: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Does `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` let an unprivileged caller exploit that under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, so that `vlmgp.totalSupply()` diverges from `sum of userInfo[vlmgp][*].amount`, the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, have the attacker run `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, then assert the victim's claimable value and the `vlmgp.totalSupply()` versus `sum of userInfo[vlmgp][*].amount` relation are unchanged by the attacker's transaction.
