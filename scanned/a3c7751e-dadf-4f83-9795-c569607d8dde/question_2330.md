# Q2330: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. With _amount and the block, with the WOM immediately converted 1:1 into mWOM under attacker control and the MGP balance is below the sum of accrued rewards, can an unprivileged caller sequence `stake(uint256 _amount)` so that `_balances[account]` and `_totalSupply` no longer reconcile, violating the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `stake(uint256 _amount)` sequence atomically under the MGP balance is below the sum of accrued rewards, asserting at the end that `_balances[account]` still equals `_totalSupply` and the PoC's balance delta is non-positive.
