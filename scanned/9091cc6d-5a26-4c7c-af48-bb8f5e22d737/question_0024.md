# Q0024: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Under the attacker is the only staker for a single block, is there an unprivileged sequence of `stake(uint256 _amount)` that leaves `_balances[account]` unreconciled with `_totalSupply`, violates the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the attacker is the only staker for a single block.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the attacker is the only staker for a single block, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
