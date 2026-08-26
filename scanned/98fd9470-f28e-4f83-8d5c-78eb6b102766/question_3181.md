# Q3181: MasterMagpie.multiclaim - safeApprove non-zero-allowance revert in _sendVlMGPFor

## Question
rewards/MasterMagpie.sol - _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Can an unprivileged attacker controlling the full _stakingTokens array, including duplicates and unregistered addresses, under a large honest deposit is sitting in the mempool and the attacker sandwiches it, exploit this through `multiclaim(address[] _stakingTokens)` to break the reconciliation between `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` and the invariant that an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: safeApprove non-zero-allowance revert in _sendVlMGPFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _sendVlMGPFor() calls IERC20(mgp).safeApprove(address(vlmgp), _amount) without first zeroing the allowance, so any residue left by a partially-consuming lockFor permanently bricks every subsequent default-pool claim. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: an approval helper on a hot path must be idempotent; a single stuck allowance must not be able to disable claiming for all users; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under a large honest deposit is sitting in the mempool and the attacker sandwiches it, asserting at the end that `userInfo[_stakingToken][user].available` still equals `userInfo[_stakingToken][user].amount` and the PoC's balance delta is non-positive.
