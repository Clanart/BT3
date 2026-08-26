# Q5728: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
Consider rewards/MasterMagpie.sol, where _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Assuming the contract is paused so only emergencyWithdraw is reachable, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is paused so only emergencyWithdraw is reachable, then assert `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` end identical in both runs.
