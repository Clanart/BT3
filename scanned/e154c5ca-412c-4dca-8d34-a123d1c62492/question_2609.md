# Q2609: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
In rewards/vlMGPBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the computed forfeit lands just above the _amount / 1000 dust threshold, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that a single misbehaving reward token must not block settlement of the remaining tokens is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting at the end that `forfeitAmount` still equals `rewardInfo.rewardPerTokenStored` and the PoC's balance delta is non-positive.
