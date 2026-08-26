# Q1490: AnkrBNBPoolHelper.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that receipt supply must fall in the same step as the backing it represents, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that receipt supply must fall in the same step as the backing it represents.
