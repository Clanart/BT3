# Q2304: Confuse replacement linkage in on_challenged_entry

## Question
Can an unprivileged attacker shape the `old_move_txid` in `ReplacementDeposit` so `on_challenged_entry` confuses replacement and non-replacement contexts, causing the reimbursement path that must remain slashable and recoverable to inherit the wrong history and violating the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/states/kickoff.rs::on_challenged_entry
- Entrypoint: public gRPC `ClementineAggregator.NewDeposit` request and the resulting deposit presigning pipeline
- Attacker controls: the `old_move_txid` in `ReplacementDeposit`
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
