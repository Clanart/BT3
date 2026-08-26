# Q2940: vlMGPBaseRewarder.getRewards - queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool

## Question
rewards/vlMGPBaseRewarder.sol: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. With the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor under attacker control and a large MGP distribution has just been queued and no account has settled yet, can an unprivileged caller sequence `getRewards(address _account, address _receiver, address[] _rewardTokens)` so that `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that only an authorised manager may decide when and by how much the reward index moves and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: queueMGP(uint256,address,address) is the MGP entry point used by MasterMagpie._sendMGPForVlMGPPool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: this additional entry point widens who can move the pool's accounting state, so the set of actors able to choose when the reward index changes is larger than the manager role the design assumes. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: only an authorised manager may decide when and by how much the reward index moves; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getRewards(address _account, address _receiver, address[] _rewardTokens)`: constrain the setup so that a large MGP distribution has just been queued and no account has settled yet, fuzz the attacker inputs (the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor), and assert after every call that only an authorised manager may decide when and by how much the reward index moves.
