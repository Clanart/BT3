# Q1931: Exploit a trust downgrade around parse_deposit_sign_session

## Question
Can an unprivileged attacker exploit the gRPC method path / `grpc-method` metadata so the request path around `parse_deposit_sign_session` silently falls back to a weaker trust model than the method expects, corrupting the caller-identity-to-RPC-method authorization boundary and breaking the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_deposit_sign_session
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: silently fall back to a weaker trust model than the method expects via the gRPC method path / `grpc-method` metadata
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
