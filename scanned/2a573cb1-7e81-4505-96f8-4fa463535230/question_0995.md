# Q995: Exploit parser ambiguity before parse_partial_sigs

## Question
Can an unprivileged attacker craft the gRPC method path / `grpc-method` metadata so the parser path before `parse_partial_sigs` and the trusted logic inside `parse_partial_sigs` disagree about what was authorized, corrupting the parsed request object that downstream signing / settlement logic trusts and breaking the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_partial_sigs
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: make parser and trusted logic disagree about what was authorized using the gRPC method path / `grpc-method` metadata
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
