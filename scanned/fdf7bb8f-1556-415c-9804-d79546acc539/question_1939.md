# Q1939: Exploit a trust downgrade around parse_partial_sigs

## Question
Can an unprivileged attacker exploit stream framing and message ordering across a long-lived gRPC stream so the request path around `parse_partial_sigs` silently falls back to a weaker trust model than the method expects, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_partial_sigs
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: stream framing and message ordering across a long-lived gRPC stream
- Exploit idea: silently fall back to a weaker trust model than the method expects via stream framing and message ordering across a long-lived gRPC stream
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
