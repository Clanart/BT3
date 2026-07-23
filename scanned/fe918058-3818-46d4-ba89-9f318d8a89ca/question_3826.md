# Q3826: Desync long-lived auth state around parse_schnorr_sig

## Question
Can an unprivileged attacker abuse long-lived stream behavior around stream framing and message ordering across a long-lived gRPC stream so authorization and message interpretation drift before `parse_schnorr_sig` acts, corrupting the internal-method guard that should only admit self-calls and violating the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_schnorr_sig
- Entrypoint: public network gRPC request attempting to cross the operator certificate/method boundary
- Attacker controls: stream framing and message ordering across a long-lived gRPC stream
- Exploit idea: let authorization and message interpretation drift across stream boundaries via stream framing and message ordering across a long-lived gRPC stream
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
