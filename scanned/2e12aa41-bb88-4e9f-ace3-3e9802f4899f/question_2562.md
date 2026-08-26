# Q2562: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
Consider rewards/MasterMagpie.sol, where _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Assuming the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, breaking the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, snapshot `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount`, run the attacker's `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
