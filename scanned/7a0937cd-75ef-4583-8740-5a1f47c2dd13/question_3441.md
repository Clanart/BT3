# Q3441: vlMGPBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
rewards/vlMGPBaseRewarder.sol: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `_calExpireForfeit(account,_amount)` unreconciled with `vlMGP.getRewardablePercentWAD(account)`, violates the invariant that a pricing helper on the claim path must never be able to permanently block settlement, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside vlMGP from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting on every row that a pricing helper on the claim path must never be able to permanently block settlement.
