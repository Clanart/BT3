# Q2879: Cross internal/public method boundary in parse_nonce_gen_first_response

## Question
Can an unprivileged attacker use the gRPC method path / `grpc-method` metadata to cross from a public path into logic that `parse_nonce_gen_first_response` assumes is only reachable internally, corrupting the caller-identity-to-RPC-method authorization boundary and violating the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_nonce_gen_first_response
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: reach logic that assumes only self-calls are possible via the gRPC method path / `grpc-method` metadata
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
