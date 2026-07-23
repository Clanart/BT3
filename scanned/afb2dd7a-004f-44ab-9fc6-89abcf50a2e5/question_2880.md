# Q2880: Cross internal/public method boundary in parse_details

## Question
Can an unprivileged attacker use the timing of requests across reconnects and client-verification toggles to cross from a public path into logic that `parse_details` assumes is only reachable internally, corrupting the internal-method guard that should only admit self-calls and violating the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_details
- Entrypoint: public network gRPC request attempting to cross the operator certificate/method boundary
- Attacker controls: the timing of requests across reconnects and client-verification toggles
- Exploit idea: reach logic that assumes only self-calls are possible via the timing of requests across reconnects and client-verification toggles
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
