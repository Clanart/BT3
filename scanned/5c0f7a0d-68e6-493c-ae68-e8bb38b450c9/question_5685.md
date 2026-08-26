# Q5685: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the contract is paused so only emergencyWithdraw is reachable, so that `vlmgp.totalSupply()` diverges from `sum of userInfo[vlmgp][*].amount`, the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaim(address[] _stakingTokens)`: constrain the setup so that the contract is paused so only emergencyWithdraw is reachable, fuzz the attacker inputs (the full _stakingTokens array, including duplicates and unregistered addresses), and assert after every call that the vlMGP reward path must remain claimable regardless of prior allowance residue.
