# Q2750: mWOMSVBaseRewarder.updateFor - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` via `updateFor(address _account)`, breaking the invariant that only an authorised manager may decide when and by how much the reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `updateFor(address _account)`, and assert `rewards[_rewardToken].historicalRewards` equals `IERC20(_rewardToken).balanceOf(address(this))` and that no account can withdraw more than it put in.
