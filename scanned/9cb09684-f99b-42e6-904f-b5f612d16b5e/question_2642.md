# Q2642: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. With _amount and the block, with the WOM immediately converted 1:1 into mWOM under attacker control and the attacker calls getReward immediately after a large stake by another user, can an unprivileged caller sequence `stake(uint256 _amount)` so that `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the attacker calls getReward immediately after a large stake by another user, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
