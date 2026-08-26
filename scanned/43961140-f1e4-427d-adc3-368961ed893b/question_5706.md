# Q5706: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Starting from a state where the contract is paused so only emergencyWithdraw is reachable, can an unprivileged EOA use `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to leave `mgpPerSec` inconsistent with `IERC20(mgp).balanceOf(masterMagpie)`, violating the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`: constrain the setup so that the contract is paused so only emergencyWithdraw is reachable, fuzz the attacker inputs (both outer and inner arrays, so every reward-token address and its order), and assert after every call that the vlMGP reward path must remain claimable regardless of prior allowance residue.
