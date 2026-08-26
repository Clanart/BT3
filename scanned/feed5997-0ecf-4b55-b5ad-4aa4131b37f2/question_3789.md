# Q3789: AnkrBNBPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/AnkrBNBPoolHelper.sol - _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that an approval on the deposit hot path must be idempotent, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_liquidity burned via burnReceiptToken` versus `the deposit-token balance delta paid out by WombatStaking.withdraw` relation are unchanged by the attacker's transaction.
