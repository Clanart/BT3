# Q990: Exploit parser ambiguity before parse_op_keys_with_deposit

## Question
Can an unprivileged attacker craft the presented peer certificate chain so the parser path before `parse_op_keys_with_deposit` and the trusted logic inside `parse_op_keys_with_deposit` disagree about what was authorized, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_op_keys_with_deposit
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the presented peer certificate chain
- Exploit idea: make parser and trusted logic disagree about what was authorized using the presented peer certificate chain
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
