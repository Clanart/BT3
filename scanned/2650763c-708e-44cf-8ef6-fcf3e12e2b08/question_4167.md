# Q4167: socialize-debt via liquidate-multi: make a required price path abort so the position can no lo

## Question
`socialize-debt` (mainnet/contracts/vault/v0-vault-stx.clar:944) writes down `lindex` by one ratio while reducing `assets` by a completely different `principal-reduction`. Can an unprivileged caller of `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), by choosing which borrowers are placed early versus late in the batch, use that to make a required price path abort so the position can no longer be closed or seized, violating the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call and producing permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:944` -> `socialize-debt`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `socialize-debt` writes down `lindex` by one ratio while reducing `assets` by a completely different `principal-reduction`. Reach it through `liquidate-multi` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `socialize-debt` touches, run `liquidate-multi` with which borrowers are placed early versus late in the batch, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
