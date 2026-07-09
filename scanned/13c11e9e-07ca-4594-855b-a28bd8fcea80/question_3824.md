# Q3824: NEAR add_token mapping writer upgraded token changes supply semantics under live bridge mappings

## Question
Can an unprivileged attacker leverage public bridge flows after `public deploy/bind flows through internal mapping writes` so that `near/omni-bridge/src/lib.rs::add_token` interacts with an upgraded token whose mint/burn semantics no longer match prior assumptions, violating `mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_token`
- Entrypoint: `public deploy/bind flows through internal mapping writes`
- Attacker controls: token id, foreign token address, decimals, and origin decimals
- Exploit idea: Even when upgrade is admin-gated, test whether public flows remain safe if token semantics drift across versions.
- Invariant to test: mapping writes must never permit duplicate foreign addresses, duplicate Near token ids, or decimal records that disagree with the actual wrapped token
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Diff pre/post-upgrade token behavior under the same public bridge flow and assert invariant preservation for supply, callbacks, and owner checks.
