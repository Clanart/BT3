# Q2989: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
In wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Does `withdraw(uint256 amount, bool claim)` let an unprivileged caller exploit that under the attacker stakes one wei so _totalSupply is non-zero but every division truncates, so that `rewards[account]` diverges from `IERC20(mgp).balanceOf(address(this))`, the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker stakes one wei so _totalSupply is non-zero but every division truncates, snapshot `rewards[account]` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
