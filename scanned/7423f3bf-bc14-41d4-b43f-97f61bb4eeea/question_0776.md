# Q0776: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
Consider rewards/MasterMagpie.sol, where _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Assuming the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, have the attacker run `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].available` versus `userInfo[_stakingToken][user].amount` relation are unchanged by the attacker's transaction.
