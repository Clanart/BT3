# Q5950: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
rewards/MasterMagpie.sol: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Under the attacker repeats the call in the same block to observe the second, no-op iteration, is there an unprivileged sequence of `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` that leaves `userInfo[_stakingToken][user].rewardDebt` unreconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`, violates the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence atomically under the attacker repeats the call in the same block to observe the second, no-op iteration, asserting at the end that `userInfo[_stakingToken][user].rewardDebt` still equals `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the PoC's balance delta is non-positive.
