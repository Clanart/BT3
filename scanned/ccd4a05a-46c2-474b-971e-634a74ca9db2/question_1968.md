# Q1968: `default_grpc_limits` and default-enabled configuration

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port exploit the default value `default_grpc_limits` in `core/src/config/mod.rs` produces or validates (a disabled client verification, a permissive network, a paramset constant such as `the module's protocol constant`) to reach a fund-moving path that the documented deployment assumes is closed?

## Target
- File/function: `core/src/config/mod.rs` -> `default_grpc_limits` (This module defines configuration options)
- Entrypoint: a request or transaction against a default-configured deployment -> `default_grpc_limits`
- Attacker controls: only the attacker's own requests; the configuration is the shipped default; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: reach a protected path because the shipped default leaves it open
- Invariant to test: the shipped default configuration closes every path that can move bridge funds to an unauthenticated caller
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: boot with defaults and assert the fund-moving path refuses an unauthenticated caller
