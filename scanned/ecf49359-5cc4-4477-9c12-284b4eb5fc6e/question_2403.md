# Q2403: Spoof the caller boundary around parse_deposit_sign_session

## Question
Can an unprivileged attacker exploit attacker-controlled the presented peer certificate chain so `parse_deposit_sign_session` executes without the intended aggregator/self identity, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_deposit_sign_session
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the presented peer certificate chain
- Exploit idea: execute privileged code without the intended identity by shaping the presented peer certificate chain
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
