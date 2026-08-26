# Q3431: AnkrBNBPoolHelper.withdraw - balance() and totalStaked() read a different ledger than the payout

## Question
In wombat/AnkrBNBPoolHelper.sol, balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `pid cached at construction` inconsistent with `pools[lpToken].pid in WombatStaking`, violating the invariant that the balance a user is shown must be the exact basis on which their withdrawal is priced and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: balance() and totalStaked() read a different ledger than the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the balance a user is shown must be the exact basis on which their withdrawal is priced; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up a residual stakingToken balance from an earlier rounding sits on the helper, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
