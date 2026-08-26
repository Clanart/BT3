# Q0695: AnkrBNBPoolHelper.withdraw - balance() and totalStaked() read a different ledger than the payout

## Question
In wombat/AnkrBNBPoolHelper.sol, balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Starting from a state where the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that the balance a user is shown must be the exact basis on which their withdrawal is priced and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: balance() and totalStaked() read a different ledger than the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the balance a user is shown must be the exact basis on which their withdrawal is priced; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence atomically under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.
