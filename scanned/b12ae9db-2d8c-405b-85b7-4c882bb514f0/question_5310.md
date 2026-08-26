# Q5310: AnkrBNBPoolHelper.harvest - deposit and withdraw both run the full harvest and fee path

## Question
wombat/AnkrBNBPoolHelper.sol: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. With the harvest timing for the whole pool under attacker control and the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged caller sequence `harvest()` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `harvest()` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (the harvest timing for the whole pool) under the attacker deposits and withdraws through the helper inside one transaction, asserting on every row that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding.
