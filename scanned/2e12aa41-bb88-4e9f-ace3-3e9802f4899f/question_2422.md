# Q2422: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
Note that in wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 amount, bool claim)` under the MGP balance is below the sum of accrued rewards and force `_totalSupply` apart from `IERC20(mWom).balanceOf(address(this))`, breaking the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the MGP balance is below the sum of accrued rewards, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `_totalSupply` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
