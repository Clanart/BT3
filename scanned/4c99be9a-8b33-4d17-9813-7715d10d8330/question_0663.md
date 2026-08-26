# Q0663: WombatPoolHelperV2.withdraw - withdraw releases the underlying before the stake check runs

## Question
wombat/WombatPoolHelperV2.sol - withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Can an unprivileged attacker controlling _liquidity and _minAmount, under the pool's deposit token is wBNB and the caller arrived through depositNative, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and the invariant that an entitlement must be verified before the value backing it leaves the protocol, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount) under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting on every row that an entitlement must be verified before the value backing it leaves the protocol.
