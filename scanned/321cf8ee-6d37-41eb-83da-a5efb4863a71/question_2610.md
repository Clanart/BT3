# Q2610: mWOMSVBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `forfeitAmount` apart from `rewardInfo.rewardPerTokenStored`, breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `forfeitAmount` versus `rewardInfo.rewardPerTokenStored` relation are unchanged by the attacker's transaction.
