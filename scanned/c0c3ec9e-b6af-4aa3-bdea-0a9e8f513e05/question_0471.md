# Q0471: vlMGPBaseRewarder.getReward - forfeit erased by settling during cooldown

## Question
In rewards/vlMGPBaseRewarder.sol, _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, and drive `totalStaked()` out of agreement with `IERC20(vlMGP).totalSupply()` - breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, call `getReward(address _account, address _receiver)`, and assert `totalStaked()` equals `IERC20(vlMGP).totalSupply()` and that no account can withdraw more than it put in.
