# Q1484: mWOMSVBaseRewarder.getReward - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Assuming the account's slot matured recently so the percent has only just begun to decay, can an unprivileged attacker turn this into a divergence between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that only an authorised manager may decide when and by how much the reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the account's slot matured recently so the percent has only just begun to decay, asserting on every row that only an authorised manager may decide when and by how much the reward index moves.
