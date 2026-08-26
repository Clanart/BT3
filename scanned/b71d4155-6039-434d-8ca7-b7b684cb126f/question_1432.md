# Q1432: mWOMSVBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
rewards/mWOMSVBaseRewarder.sol - queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the account's slot matured recently so the percent has only just begun to decay, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` and the invariant that a single misbehaving reward token must not block settlement of the remaining tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account's slot matured recently so the percent has only just begun to decay, call `getReward(address _account, address _receiver)`, and assert `totalStaked()` equals `IERC20(mWOMSV).totalSupply()` and that no account can withdraw more than it put in.
