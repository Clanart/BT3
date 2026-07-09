# Q3761: NEAR OmniToken mint cleanup order around callbacks reopens or strands value

## Question
Can an unprivileged attacker trigger `public bridge-token mint path via controller-only callback reached from bridge delivery` so that `near/omni-token/src/lib.rs::mint` cleans up transfer or fast-transfer state in an order that either reopens replay or strands user funds after callback failure, violating `mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes`?

## Target
- File/function: `near/omni-token/src/lib.rs::mint`
- Entrypoint: `public bridge-token mint path via controller-only callback reached from bridge delivery`
- Attacker controls: recipient account, amount, optional `msg`, and any receiver behavior in `ft_transfer_call`
- Exploit idea: Focus on removal of pending records, finalization flags, and lock rollback relative to payout callbacks.
- Invariant to test: mint-with-message and plain mint must be economically equivalent and must not create balances on the controller or recipient that survive inconsistent callback outcomes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Inject failures at each callback boundary and assert that cleanup always leaves one consistent recoverable state.
