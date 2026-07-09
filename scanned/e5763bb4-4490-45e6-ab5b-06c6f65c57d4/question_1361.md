# Q1361: NEAR process_fin_transfer_to_other_chain asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `internal path reached from public `fin_transfer` for non-Near recipients` to make `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain` release value through a more favorable branch than the source event actually authorized because of marks the transfer finalised, unlocks origin-side liquidity, re-locks destination-side fee or amount, optionally sends the fast-transfer payout to a relayer, or stores a new pending transfer for the next chain, violating `cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::process_fin_transfer_to_other_chain`
- Entrypoint: `internal path reached from public `fin_transfer` for non-Near recipients`
- Attacker controls: recipient chain, predecessor account, transfer message, fast-transfer status, and token origin chain
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: cross-chain forwarding must never let one verified inbound event release value locally and also create a second valid outbound claim with inconsistent lock accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
