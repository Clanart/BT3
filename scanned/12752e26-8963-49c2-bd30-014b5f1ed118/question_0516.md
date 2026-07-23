# Q516: Cross internal/public method boundary in parse_next_deposit_finalize_param_schnorr_sig

## Question
Can an unprivileged attacker use the gRPC method path / `grpc-method` metadata to cross from a public path into logic that `parse_next_deposit_finalize_param_schnorr_sig` assumes is only reachable internally, corrupting the internal-method guard that should only admit self-calls and violating the invariant that parser logic must not turn attacker-controlled bytes into a trusted privileged request under a different meaning, leading to High. Role/pausing logic vulnerabilities that allow an unprivileged attacker to bypass safety controls?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_next_deposit_finalize_param_schnorr_sig
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: reach logic that assumes only self-calls are possible via the gRPC method path / `grpc-method` metadata
- Invariant to test: parser logic must not turn attacker-controlled bytes into a trusted privileged request under a different meaning
- Expected Immunefi impact: High. Role/pausing logic vulnerabilities that allow an unprivileged attacker to bypass safety controls
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
