# Q5854: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
In rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, so that `mgpPerSec` diverges from `IERC20(mgp).balanceOf(masterMagpie)`, the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting at the end that `mgpPerSec` still equals `IERC20(mgp).balanceOf(masterMagpie)` and the PoC's balance delta is non-positive.
