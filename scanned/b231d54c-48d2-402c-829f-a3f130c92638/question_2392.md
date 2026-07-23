# Q2392: Cross-wire presigning material in transfer_to_btc_wallet

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineOperator.TransferToBtcWallet` to make `transfer_to_btc_wallet` mix nonce, signature, or key material across two otherwise valid sessions via attacker-controlled the `deposit_outpoint` and its on-chain prevout details, so the operator signature set attached to a deposit is authorized under the wrong context and the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context breaks, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/operator.rs::transfer_to_btc_wallet
- Entrypoint: auth-bypass attempt into gRPC `ClementineOperator.TransferToBtcWallet`
- Attacker controls: the `deposit_outpoint` and its on-chain prevout details
- Exploit idea: mix nonces, partial signatures, or saved signatures across otherwise valid sessions
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
