# Q0148: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol - stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the attacker is the only staker for a single block, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` and the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the attacker is the only staker for a single block.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only staker for a single block, snapshot `_totalSupply` and `IERC20(mWom).balanceOf(address(this))`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
