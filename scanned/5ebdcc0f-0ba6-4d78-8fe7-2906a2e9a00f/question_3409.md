# Q3409: Bypass settlement gating in get_payout_tx_blockhash_derivation

## Question
Can an unprivileged attacker craft the requested `output_script_pubkey` so `get_payout_tx_blockhash_derivation` satisfies its local gating checks for the wrong bridge action, corrupting the payout destination or payout amount and violating the invariant that withdrawal retries must not create two valid settlement paths for the same bridge claim, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/bitvm_client.rs::get_payout_tx_blockhash_derivation
- Entrypoint: public gRPC `ClementineAggregator.Withdraw` request
- Attacker controls: the requested `output_script_pubkey`
- Exploit idea: make local checks pass for the wrong bridge action via the requested `output_script_pubkey`
- Invariant to test: withdrawal retries must not create two valid settlement paths for the same bridge claim
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that mutates one withdrawal field at a time across repeated requests and assert no second valid payout / reimbursement path appears
