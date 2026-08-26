# Q0290: WombatPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/WombatPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an unprivileged attacker reach this through `depositLP(uint256 _lpAmount)` while the pool's deposit token is wBNB and the caller arrived through depositNative, and drive `_liquidity burned via burnReceiptToken` out of agreement with `the deposit-token balance delta paid out by WombatStaking.withdraw` - breaking the invariant that an approval on the deposit hot path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's deposit token is wBNB and the caller arrived through depositNative, call `depositLP(uint256 _lpAmount)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
