# Q2533: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
In rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Does `useCode(bytes32 _code)` let an unprivileged caller exploit that under the attacker cancels a cooldown so their real lock rises with no factor refresh, so that `myReferer[account]` diverges from `userInfos[account].codeIUsed`, the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker cancels a cooldown so their real lock rises with no factor refresh, then assert `myReferer[account]` and `userInfos[account].codeIUsed` end identical in both runs.
