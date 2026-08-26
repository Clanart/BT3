# Q5060: AnkrBNBPoolHelper.withdraw - balance() and totalStaked() read a different ledger than the payout

## Question
In wombat/AnkrBNBPoolHelper.sol, balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Can an unprivileged attacker reach this through `withdraw(uint256 _liquidity, uint256 _minAmount)` while the attacker has moved the wom/mWom Wombat pool immediately before calling, and drive `IERC20(stakingToken).totalSupply()` out of agreement with `the MasterWombat staked balance for pid` - breaking the invariant that the balance a user is shown must be the exact basis on which their withdrawal is priced - for Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: balance() and totalStaked() read a different ledger than the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: balance(address) and totalStaked() read MasterMagpie stakingInfo and the receipt token supply, while withdraw pays a Wombat deposit-token balance delta, so the accounting a user sees and the value they receive come from unrelated sources. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the balance a user is shown must be the exact basis on which their withdrawal is priced; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the attacker has moved the wom/mWom Wombat pool immediately before calling, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that the balance a user is shown must be the exact basis on which their withdrawal is priced.
