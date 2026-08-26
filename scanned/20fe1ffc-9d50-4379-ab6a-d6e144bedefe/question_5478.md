# Q5478: AnkrBNBPoolHelper.withdraw - balance() and totalStaked() read a different ledger than the payout

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Assuming the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that the balance a user is shown must be the exact basis on which their withdrawal is priced and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: balance() and totalStaked() read a different ledger than the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: the balance a user is shown must be the exact basis on which their withdrawal is priced; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the receipt token is minted to the helper while the credit is directed at a different address, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that the balance a user is shown must be the exact basis on which their withdrawal is priced.
