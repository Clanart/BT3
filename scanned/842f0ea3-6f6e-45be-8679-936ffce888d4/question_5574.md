# Q5574: AnkrBNBPoolHelper.depositLP - deposit and withdraw both run the full harvest and fee path

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, asserting on every row that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding.
