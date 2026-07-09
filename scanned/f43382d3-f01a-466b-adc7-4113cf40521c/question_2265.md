# Q2265: NEAR add_token mapping writer fake bridge-controlled token accepted as canonical via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public deploy/bind flows through internal mapping writes` and then replay or reorder later bind, deploy, or metadata-consumption step so that `near/omni-bridge/src/lib.rs::add_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `fake bridge-controlled token accepted as canonical` under writes the core `token_id_to_address`, `token_address_to_id`, and `token_decimals` state that every bridge path later trusts, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
