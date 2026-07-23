# Q994: Exploit parser ambiguity before parse_schnorr_sig

## Question
Can an unprivileged attacker craft the presented peer certificate chain so the parser path before `parse_schnorr_sig` and the trusted logic inside `parse_schnorr_sig` disagree about what was authorized, corrupting the internal-method guard that should only admit self-calls and breaking the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/operator.rs::parse_schnorr_sig
- Entrypoint: public network gRPC request attempting to cross the operator certificate/method boundary
- Attacker controls: the presented peer certificate chain
- Exploit idea: make parser and trusted logic disagree about what was authorized using the presented peer certificate chain
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
