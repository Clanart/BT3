# Q1312: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
wombat/WomUp.sol: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Under the reward period has just ended so periodFinish is behind block.timestamp, is there an unprivileged sequence of `stake(uint256 _amount)` that leaves `rewards[account]` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the reward period has just ended so periodFinish is behind block.timestamp, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `rewards[account]` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
