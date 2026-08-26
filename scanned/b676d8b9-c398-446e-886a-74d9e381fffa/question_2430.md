# Q2430: mWOMSVBaseRewarder.getRewards - donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens

## Question
In rewards/mWOMSVBaseRewarder.sol, this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Starting from a state where the computed forfeit lands just above the _amount / 1000 dust threshold, can an unprivileged EOA use `getRewards(address _account, address _receiver, address[] _rewardTokens)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: donateRewards(uint256,address) is additionally exposed to any caller for already-registered reward tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that the computed forfeit lands just above the _amount / 1000 dust threshold, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that only an authorised manager may decide when and by how much the reward index moves.
