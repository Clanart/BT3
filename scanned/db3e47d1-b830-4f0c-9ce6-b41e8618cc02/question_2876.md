# Q2876: Cross internal/public method boundary in parse_next_deposit_finalize_param_schnorr_sig

## Question
Can an unprivileged attacker use the gRPC method path / `grpc-method` metadata to cross from a public path into logic that `parse_next_deposit_finalize_param_schnorr_sig` assumes is only reachable internally, corrupting the caller-identity-to-RPC-method authorization boundary and violating the invariant that only the intended aggregator or self identity may reach privileged operator/verifier methods, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/rpc/parser/verifier.rs::parse_next_deposit_finalize_param_schnorr_sig
- Entrypoint: public network gRPC request attempting to cross the verifier certificate/method boundary
- Attacker controls: the gRPC method path / `grpc-method` metadata
- Exploit idea: reach logic that assumes only self-calls are possible via the gRPC method path / `grpc-method` metadata
- Invariant to test: only the intended aggregator or self identity may reach privileged operator/verifier methods
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: stand up the gRPC service with client verification enabled and fuzz peer-cert / method-path / stream-order combinations; assert the request never reaches the privileged body
