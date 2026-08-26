# Q5113: WombatPoolHelperV2.withdraw - withdraw releases the underlying before the stake check runs

## Question
Note that in wombat/WombatPoolHelperV2.sol, withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 _liquidity, uint256 _minAmount)` under the attacker has moved the wom/mWom Wombat pool immediately before calling and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that an entitlement must be verified before the value backing it leaves the protocol for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has moved the wom/mWom Wombat pool immediately before calling, have the attacker run `withdraw(uint256 _liquidity, uint256 _minAmount)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
