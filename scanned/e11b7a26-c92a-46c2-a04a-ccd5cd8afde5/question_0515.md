# Q515: Cross internal/public method boundary in parse_deposit_sign_session

## Question
Can an unprivileged attacker use stream framing and message ordering across a long-lived gRPC stream to cross from a public path into logic that `parse_deposit_sign_session` assumes is only reachable internally, corrupting the caller-identity-to-RPC-method authorization boundary and violating the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_deposit_sign_session
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: stream framing and message ordering across a long-lived gRPC stream
- Exploit idea: reach logic that assumes only self-calls are possible via stream framing and message ordering across a long-lived gRPC stream
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
