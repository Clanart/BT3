# Q4214: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
Consider wombat/WombatPoolHelperV2.sol, where depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Assuming the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged attacker turn this into a divergence between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` via `deposit(uint256 _amount, uint256 _minimumLiquidity)`, breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.
