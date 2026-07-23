# Q1920: Confuse replacement linkage in transfer_to_btc_wallet

## Question
Can an unprivileged attacker shape the set of verifier, operator, or watchtower keys that get associated with the deposit context so `transfer_to_btc_wallet` confuses replacement and non-replacement contexts, causing the verifier nonce session that a final signature is supposed to consume to inherit the wrong history and violating the invariant that each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::transfer_to_btc_wallet
- Entrypoint: auth-bypass attempt into gRPC `ClementineOperator.TransferToBtcWallet`
- Attacker controls: the set of verifier, operator, or watchtower keys that get associated with the deposit context
- Exploit idea: make replacement and non-replacement data bleed across one trusted path
- Invariant to test: each deposit context must map to exactly one verifier session, one signer set, and one move/emergency-stop bundle
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
