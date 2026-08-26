# Q3941: mWOMSVBaseRewarder.getReward - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Starting from a state where totalStaked is zero and queuedRewards holds a backlog, can an unprivileged EOA use `getReward(address _account, address _receiver)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under totalStaked is zero and queuedRewards holds a backlog, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
