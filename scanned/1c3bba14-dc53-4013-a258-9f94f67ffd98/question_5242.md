# Q5242: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. With the full _stakingTokens array, including duplicates and unregistered addresses under attacker control and the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged caller sequence `multiclaim(address[] _stakingTokens)` so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` no longer reconcile, violating the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), asserting at the end that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` still equals `block.timestamp` and the PoC's balance delta is non-positive.
