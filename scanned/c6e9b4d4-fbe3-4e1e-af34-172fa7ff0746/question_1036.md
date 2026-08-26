# Q1036: AnkrBNBPoolHelper.deposit - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/AnkrBNBPoolHelper.sol: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. With _amount and _minimumLiquidity under attacker control and the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that an approval on the deposit hot path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _minimumLiquidity) under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting on every row that an approval on the deposit hot path must be idempotent.
