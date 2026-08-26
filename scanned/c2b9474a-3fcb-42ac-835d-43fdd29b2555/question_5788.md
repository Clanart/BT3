# Q5788: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol - _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an unprivileged attacker controlling both outer and inner arrays, so every reward-token address and its order, under the victim has a large unClaimedMgp balance that has not been settled for several epochs, exploit this through `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to break the reconciliation between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` and the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unClaimedMgp balance that has not been settled for several epochs, snapshot `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)`, run the attacker's `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
