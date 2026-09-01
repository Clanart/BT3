# Q3308: `get_next_pub_nonces` is reachable with no client certificate

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port call `get_next_pub_nonces` in `core/src/rpc/aggregator.rs` on a deployment where `client_verification` is false - the state in which `create_grpc_server` installs `Interceptors::Noop` and no `only_aggregator_and_self` check runs - and thereby reach a state-changing or signing path that is meant to be reserved for the aggregator?

## Target
- File/function: `core/src/rpc/aggregator.rs` -> `get_next_pub_nonces`
- Entrypoint: a direct gRPC request to the aggregator/entity port -> `get_next_pub_nonces`
- Attacker controls: the whole request body and the choice of method; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: invoke a privileged protocol method with no credential at all
- Invariant to test: a caller reaching `get_next_pub_nonces` holds the certificate `only_aggregator_and_self` requires
- Expected Immunefi impact: High - auth bypass: an unprivileged caller reaches a state-changing or signing path reserved for the aggregator
- Fast validation: start the server with `client_verification` off and assert `get_next_pub_nonces` refuses an uncertified caller
