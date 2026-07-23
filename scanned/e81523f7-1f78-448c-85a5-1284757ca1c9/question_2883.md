# Q2883: Cross internal/public method boundary in parse_partial_sigs

## Question
Can an unprivileged attacker use the timing of requests across reconnects and client-verification toggles to cross from a public path into logic that `parse_partial_sigs` assumes is only reachable internally, corrupting the caller-identity-to-RPC-method authorization boundary and violating the invariant that parser logic must not turn attacker-controlled bytes into a trusted privileged request under a different meaning, leading to High. Role/pausing logic vulnerabilities that allow an unprivileged attacker to bypass safety controls?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_partial_sigs
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the timing of requests across reconnects and client-verification toggles
- Exploit idea: reach logic that assumes only self-calls are possible via the timing of requests across reconnects and client-verification toggles
- Invariant to test: parser logic must not turn attacker-controlled bytes into a trusted privileged request under a different meaning
- Expected Immunefi impact: High. Role/pausing logic vulnerabilities that allow an unprivileged attacker to bypass safety controls
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
