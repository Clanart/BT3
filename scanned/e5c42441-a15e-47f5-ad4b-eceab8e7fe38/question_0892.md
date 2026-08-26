# Q0892: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
In wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Starting from a state where _totalSupply exceeds the mWOM balance the contract actually holds, can an unprivileged EOA use `stake(uint256 _amount)` to leave `rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[account]`, violating the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `stake(uint256 _amount)` sequence atomically under _totalSupply exceeds the mWOM balance the contract actually holds, asserting at the end that `rewardPerTokenStored` still equals `userRewardPerTokenPaid[account]` and the PoC's balance delta is non-positive.
