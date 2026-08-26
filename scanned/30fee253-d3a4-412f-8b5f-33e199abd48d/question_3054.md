# Q3054: WombatPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
Consider wombat/WombatPoolHelper.sol, where withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Assuming the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that an entitlement must be verified before the value backing it leaves the protocol and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta) under the caller sets _minAmount to zero on the withdrawal leg, asserting on every row that an entitlement must be verified before the value backing it leaves the protocol.
