# Q0662: WombatPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
Consider wombat/WombatPoolHelper.sol, where withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that an entitlement must be verified before the value backing it leaves the protocol and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the pool's deposit token is wBNB and the caller arrived through depositNative, have the attacker run `withdraw(uint256 _liquidity, uint256 _minAmount)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
