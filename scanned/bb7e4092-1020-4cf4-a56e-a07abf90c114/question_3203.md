# Q3203: vlMGPBaseRewarder.updateFor - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
rewards/vlMGPBaseRewarder.sol: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, is there an unprivileged sequence of `updateFor(address _account)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `vlMGP.getRewardablePercentWAD(account)`, violates the invariant that only an authorised manager may decide when and by how much the reward index moves, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, snapshot `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
