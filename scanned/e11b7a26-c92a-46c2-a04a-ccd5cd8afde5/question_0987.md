# Q987: Exploit parser ambiguity before parse_deposit_sign_session

## Question
Can an unprivileged attacker craft the raw request bytes that parser code maps into trusted RPC parameters so the parser path before `parse_deposit_sign_session` and the trusted logic inside `parse_deposit_sign_session` disagree about what was authorized, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_deposit_sign_session
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the raw request bytes that parser code maps into trusted RPC parameters
- Exploit idea: make parser and trusted logic disagree about what was authorized using the raw request bytes that parser code maps into trusted RPC parameters
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
