# Q1777: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. With amount and whether the claim leg runs in the same call under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `withdraw(uint256 amount, bool claim)` so that `rewardRate * duration` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 amount, bool claim)`: constrain the setup so that the target helper leaves a non-zero allowance after depositFor, fuzz the attacker inputs (amount and whether the claim leg runs in the same call), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
