# Q5776: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Can an unprivileged attacker reach this through `multiclaim(address[] _stakingTokens)` while the victim has a large unClaimedMgp balance that has not been settled for several epochs, and drive `mgpPerSec` out of agreement with `IERC20(mgp).balanceOf(masterMagpie)` - breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unClaimedMgp balance that has not been settled for several epochs, then assert `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` end identical in both runs.
