# Q4015: vlMGPBaseRewarder.updateFor - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
In rewards/vlMGPBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Starting from a state where the attacker locks one block before a known large settlement and unlocks one block after, can an unprivileged EOA use `updateFor(address _account)` to leave `totalStaked()` inconsistent with `IERC20(vlMGP).totalSupply()`, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the attacker locks one block before a known large settlement and unlocks one block after, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that only an authorised manager may decide when and by how much the reward index moves.
