# Q3827: Desync long-lived auth state around parse_partial_sigs

## Question
Can an unprivileged attacker abuse long-lived stream behavior around the presented peer certificate chain so authorization and message interpretation drift before `parse_partial_sigs` acts, corrupting the parsed request object that downstream signing / settlement logic trusts and violating the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_partial_sigs
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the presented peer certificate chain
- Exploit idea: let authorization and message interpretation drift across stream boundaries via the presented peer certificate chain
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
