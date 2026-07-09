# Q1428: Starknet BridgeToken mint global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public settlement-side mint path reached from `fin_transfer`` with the code paths summarized by `starknet/src/bridge_token.cairo::mint` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by mints wrapped supply into the recipient account under control of the omni bridge, violating `minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case`?

## Target
- File/function: `starknet/src/bridge_token.cairo::mint`
- Entrypoint: `public settlement-side mint path reached from `fin_transfer``
- Attacker controls: recipient address, amount, and any receiver-side behavior after receiving bridged tokens
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: minted supply must only arise from one validated settlement event and must not survive a later rollback or replay edge case
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
