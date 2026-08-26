# Q2091: AnkrBNBPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Assuming the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged attacker turn this into a divergence between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that an approval on the deposit hot path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_minimumLiquidity supplied by the caller` versus `the LP actually minted by the Wombat pool` relation are unchanged by the attacker's transaction.
