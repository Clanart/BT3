# Q5856: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Can an unprivileged attacker reach this through `multiclaim(address[] _stakingTokens)` while the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, and drive `userInfo[_stakingToken][user].amount` out of agreement with `_calLpSupply(_stakingToken)` - breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, snapshot `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)`, run the attacker's `multiclaim(address[] _stakingTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
