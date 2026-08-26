# Q4908: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool

## Question
rewards/MasterMagpie.sol - _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Can an unprivileged attacker controlling the full _stakingTokens array, including duplicates and unregistered addresses, under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, exploit this through `multiclaim(address[] _stakingTokens)` to break the reconciliation between `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` and the invariant that the vlMGP reward path must remain claimable regardless of prior allowance residue, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendMGPForVlMGPPool() calls IERC20(mgp).safeApprove(vlMGPRewarder, _amount) with no reset, so leftover allowance on the vlMGP rewarder path makes every vlMGP-pool claim revert for every user at once. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: the vlMGP reward path must remain claimable regardless of prior allowance residue; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the full _stakingTokens array, including duplicates and unregistered addresses) under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, asserting on every row that the vlMGP reward path must remain claimable regardless of prior allowance residue.
