# Q2119: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
rewards/ReferralStorage.sol: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Under the attacker locked vlMGP before registering a code, is there an unprivileged sequence of `useCode(bytes32 _code)` that leaves `codeOwners[_code]` unreconciled with `userInfos[account].myCode`, violates the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker locked vlMGP before registering a code, call `useCode(bytes32 _code)`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
