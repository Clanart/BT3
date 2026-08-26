# Q5601: MasterMagpie.multiclaimFor - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
In rewards/MasterMagpie.sol, _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Starting from a state where the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, can an unprivileged EOA use `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` to leave `mgpPerSec` inconsistent with `IERC20(mgp).balanceOf(masterMagpie)`, violating the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, call `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, and assert `mgpPerSec` equals `IERC20(mgp).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
