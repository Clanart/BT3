# Q3355: Exploit parser ambiguity before parse_partial_sigs

## Question
Can an unprivileged attacker craft the gRPC method path / `grpc-method` metadata so the parser path before `parse_partial_sigs` and the trusted logic inside `parse_partial_sigs` disagree about what was authorized, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_partial_sigs
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: make parser and trusted logic disagree about what was authorized using the gRPC method path / `grpc-method` metadata
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
