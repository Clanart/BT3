# Q5968: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
Consider rewards/MasterMagpie.sol, where _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Assuming the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged attacker turn this into a divergence between `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats the call in the same block to observe the second, no-op iteration, call `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, and assert `unClaimedMgp[_stakingToken][user]` equals `userInfo[_stakingToken][user].rewardDebt` and that no account can withdraw more than it put in.
