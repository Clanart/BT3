# Q1016: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
In wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while _totalSupply exceeds the mWOM balance the contract actually holds, and drive `rewards[account]` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 amount, bool claim)`: constrain the setup so that _totalSupply exceeds the mWOM balance the contract actually holds, fuzz the attacker inputs (amount and whether the claim leg runs in the same call), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
