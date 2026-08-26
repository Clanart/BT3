# Q4685: AnkrBNBPoolHelper.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Assuming an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, can an unprivileged attacker turn this into a divergence between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that receipt supply must fall in the same step as the backing it represents and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence atomically under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.
