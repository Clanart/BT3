# Q5361: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Starting from a state where the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged EOA use `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` to leave `vlmgp.totalSupply()` inconsistent with `sum of userInfo[vlmgp][*].amount`, violating the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), have the attacker run `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, then assert the victim's claimable value and the `vlmgp.totalSupply()` versus `sum of userInfo[vlmgp][*].amount` relation are unchanged by the attacker's transaction.
