# Q2995: vlMGPBaseRewarder.getReward - forfeited value recycled to the same stakers who forfeited it

## Question
Note that in rewards/vlMGPBaseRewarder.sol, _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under a large MGP distribution has just been queued and no account has settled yet and force `totalStaked()` apart from `IERC20(vlMGP).totalSupply()`, breaking the invariant that value forfeited on exit must accrue to the users who remained committed, not back to the exiting account for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeited value recycled to the same stakers who forfeited it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _sendReward() routes forfeitAmount into _queueNewRewardsWithoutTransfer(), which raises rewardPerTokenStored for everyone currently staked including the forfeiting account, so a dominant staker recovers most of their own forfeit. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: value forfeited on exit must accrue to the users who remained committed, not back to the exiting account; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under a large MGP distribution has just been queued and no account has settled yet, asserting at the end that `totalStaked()` still equals `IERC20(vlMGP).totalSupply()` and the PoC's balance delta is non-positive.
