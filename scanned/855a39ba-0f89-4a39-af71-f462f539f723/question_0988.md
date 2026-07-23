# Q988: Exploit parser ambiguity before parse_next_deposit_finalize_param_schnorr_sig

## Question
Can an unprivileged attacker craft the presented peer certificate chain so the parser path before `parse_next_deposit_finalize_param_schnorr_sig` and the trusted logic inside `parse_next_deposit_finalize_param_schnorr_sig` disagree about what was authorized, corrupting the parsed request object that downstream signing / settlement logic trusts and breaking the invariant that internal/public RPC boundaries must not depend on attacker-malleable metadata alone, leading to Critical. Direct loss of funds?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_next_deposit_finalize_param_schnorr_sig
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the presented peer certificate chain
- Exploit idea: make parser and trusted logic disagree about what was authorized using the presented peer certificate chain
- Invariant to test: internal/public RPC boundaries must not depend on attacker-malleable metadata alone
- Expected Immunefi impact: Critical. Direct loss of funds
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
