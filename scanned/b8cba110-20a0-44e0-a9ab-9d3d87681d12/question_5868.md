# Q5868: MasterMagpie.multiclaimSpec - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol - _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an unprivileged attacker controlling both outer and inner arrays, so every reward-token address and its order, under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, exploit this through `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to break the reconciliation between `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` and the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence atomically under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting at the end that `userInfo[_stakingToken][user].amount` still equals `_calLpSupply(_stakingToken)` and the PoC's balance delta is non-positive.
