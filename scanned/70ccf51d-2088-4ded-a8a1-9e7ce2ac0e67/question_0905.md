# Q0905: vlMGPBaseRewarder.updateFor - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
In rewards/vlMGPBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Does `updateFor(address _account)` let an unprivileged caller exploit that under the account's slot matured recently so the percent has only just begun to decay, so that `totalStaked()` diverges from `IERC20(vlMGP).totalSupply()`, the invariant that only an authorised manager may decide when and by how much the reward index moves is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account's slot matured recently so the percent has only just begun to decay, call `updateFor(address _account)`, and assert `totalStaked()` equals `IERC20(vlMGP).totalSupply()` and that no account can withdraw more than it put in.
