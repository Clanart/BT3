# Q2894: ReferralStorage.useCode - self-referral across two addresses the same person controls

## Question
Note that in rewards/ReferralStorage.sol, useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Can an attacker holding only tokens bought on market reach it via `useCode(bytes32 _code)` under the attacker splits one large lock across many addresses that each register a code and force `BoostPoint` apart from `totalBoostFactor`, breaking the invariant that a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: self-referral across two addresses the same person controls)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: useCode() only rejects codeOwners[_code] == msg.sender, so an attacker registers a code on one address and binds it from a second address they also control, then collects both the referrer and the referee share of their own emissions. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a referral bonus must reward genuine third-party recruitment, not a single actor operating two addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker splits one large lock across many addresses that each register a code, call `useCode(bytes32 _code)`, and assert `BoostPoint` equals `totalBoostFactor` and that no account can withdraw more than it put in.
