# Q3470: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Can an unprivileged attacker reach this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` while a large honest deposit is sitting in the mempool and the attacker sandwiches it, and drive `_calLpSupply(_stakingToken)` out of agreement with `IERC20(_stakingToken).balanceOf(masterMagpie)` - breaking the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a large honest deposit is sitting in the mempool and the attacker sandwiches it, snapshot `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)`, run the attacker's `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
