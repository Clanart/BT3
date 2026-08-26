# Q4690: vlMGPBaseRewarder.getRewards - forfeit erased by settling during cooldown

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Assuming a registered reward token has begun reverting on transfer, can an unprivileged attacker turn this into a divergence between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` via `getRewards(address _account, address _receiver, address[] _rewardTokens)`, breaking the invariant that the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getRewards(address _account, address _receiver, address[] _rewardTokens)` (mechanism: forfeit erased by settling during cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getRewards(address _account, address _receiver, address[] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the reward-token array, reachable through MasterMagpie.multiclaimSpec and multiclaimFor
- Exploit idea: _calExpireForfeit() multiplies the pending amount by vlMGP.getRewardablePercentWAD(_account), which stays at exactly 1e18 for the whole cooldown window, so a user who has already started their exit forfeits nothing. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: the forfeit must reflect the lock commitment actually served, not the settlement instant the user chose; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a registered reward token has begun reverting on transfer, call `getRewards(address _account, address _receiver, address[] _rewardTokens)`, and assert `forfeitAmount` equals `rewardInfo.rewardPerTokenStored` and that no account can withdraw more than it put in.
