# Q43: NEAR unlock_tokens_if_needed asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `internal helper reached from public finalize paths` to make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` release value through a more favorable branch than the source event actually authorized because of unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
