# Q4412: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
In rewards/MasterMagpie.sol, _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Starting from a state where the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, can an unprivileged EOA use `multiclaim(address[] _stakingTokens)` to leave `unClaimedMgp[_stakingToken][user]` inconsistent with `userInfo[_stakingToken][user].rewardDebt`, violating the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, have the attacker run `multiclaim(address[] _stakingTokens)`, then assert the victim's claimable value and the `unClaimedMgp[_stakingToken][user]` versus `userInfo[_stakingToken][user].rewardDebt` relation are unchanged by the attacker's transaction.
