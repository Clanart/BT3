# Q1658: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Starting from a state where the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged EOA use `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to leave `userInfo[_stakingToken][user].available` inconsistent with `userInfo[_stakingToken][user].amount`, violating the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (both outer and inner arrays, so every reward-token address and its order) under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, asserting on every row that the vlMGP reward path must remain claimable regardless of prior allowance residue.
