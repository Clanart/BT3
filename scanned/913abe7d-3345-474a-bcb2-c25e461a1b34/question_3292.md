# Q3292: NEAR add_token mapping writer asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public deploy/bind flows through internal mapping writes` so that `near/omni-bridge/src/lib.rs::add_token` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
