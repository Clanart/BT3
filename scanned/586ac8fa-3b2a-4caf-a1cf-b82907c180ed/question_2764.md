# Q2764: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
Note that in rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Can an attacker holding only tokens bought on market reach it via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates and force `unClaimedMgp[_stakingToken][user]` apart from `userInfo[_stakingToken][user].rewardDebt`, breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, snapshot `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt`, run the attacker's `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
