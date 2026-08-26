# Q1679: WomUp.stake - stake takes WOM but withdraw pays mWOM

## Question
Note that in wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Can an attacker holding only tokens bought on market reach it via `stake(uint256 _amount)` under the target helper leaves a non-zero allowance after depositFor and force `lastUpdateTime` apart from `periodFinish`, breaking the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `stake(uint256 _amount)`: constrain the setup so that the target helper leaves a non-zero allowance after depositFor, fuzz the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
