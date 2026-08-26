# Q1356: WombatPoolHelperV2.depositLP - deposit and withdraw both run the full harvest and fee path

## Question
In wombat/WombatPoolHelperV2.sol, WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting on every row that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding.
