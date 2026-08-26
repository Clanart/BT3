# Q5224: WombatPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
wombat/WombatPoolHelper.sol - withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Can an unprivileged attacker controlling _liquidity and _minAmount, with the payout measured as a balance delta, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that an entitlement must be verified before the value backing it leaves the protocol, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence atomically under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.
