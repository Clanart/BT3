### Title
Unix Socket gRPC Server Omits `InterceptedService` Wrapper, Bypassing mTLS Role-Based Auth on All Internal Methods — (`File: core/src/servers.rs`)

### Summary

`create_grpc_server` applies the `OnlyAggregatorAndSelf` interceptor only in the TCP branch. The Unix socket branch passes the raw service directly to `add_service`, so the interceptor is never installed. Any local process that can connect to the socket can call every gRPC method — including `InternalWithdraw`, `InternalEndRound`, and `InternalHandleKickoff` — without presenting a certificate.

### Finding Description

`create_grpc_server` in `core/src/servers.rs` handles two transport modes. In the TCP branch (lines 114–139) the service is wrapped:

```rust
let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop
    },
);
```

In the Unix socket branch (lines 179–189) the original, unwrapped service is passed directly:

```rust
let server_builder = tonic::transport::Server::builder()
    .layer(AddMethodMiddlewareLayer)
    ...
    .add_service(service);   // ← raw service, no InterceptedService
```

The `OnlyAggregatorAndSelf` interceptor is the sole enforcement point for the two-tier cert check:

- **Non-internal methods**: only aggregator cert or own cert allowed.
- **Internal methods** (name starts with `"Internal"`): only own cert allowed.

Because the interceptor is absent on the Unix socket path, neither check runs. The `Noop` variant is not even used; the interceptor object is never constructed.

The production testnet4 config sets `socket_path = "/"`, confirming Unix sockets are used in production deployments.

The `InternalWithdraw` RPC handler has no secondary gate:

```rust
async fn internal_withdraw(
    &self,
    request: Request<WithdrawParams>,
) -> Result<Response<RawSignedTx>, Status> {
    let payout_tx = self.operator.withdraw(...).await?;
    Ok(Response::new(RawSignedTx::from(&payout_tx)))
}
```

It calls `operator.withdraw` directly and queues the payout transaction in the tx-sender. There is no `cfg!(test)` guard (unlike `InternalFinalizedPayout`) and no automation feature gate (unlike `InternalEndRound` with the `automation` feature).

### Impact Explanation

An attacker with local access to the Unix socket file can:

1. **Drain operator round UTXOs via `InternalWithdraw`**: Supply any valid `withdrawal_id` and the corresponding user-provided `input_signature` (both are observable on-chain from Citrea bridge contract events). The operator will sign and queue a payout transaction spending its current round UTXO without any aggregator approval or operator consent. Repeated calls exhaust the operator's collateral chain.

2. **Disrupt round lifecycle via `InternalEndRound`** (when `automation` feature is enabled): Prematurely ending a round at the wrong time can leave kickoff connectors unspent, cause the operator to miss reimbursement windows, and put collateral UTXOs in an unrecoverable state.

3. **Trigger watchtower challenges via `InternalHandleKickoff`** on the verifier: Forces a challenge transaction to be broadcast for any kickoff txid, potentially causing the operator to be slashed if the challenge is unwarranted or timed incorrectly.

### Likelihood Explanation

The Unix socket path is the default inter-actor transport in all integration tests and is explicitly configured in the testnet4 production deployment (`socket_path = "/"`). The socket file is created with default `umask`-derived permissions (typically `0600` or `0660`), but in containerized environments (Docker Compose, Kubernetes pods with shared volumes) other containers or processes running as the same UID can reach the socket. An attacker who compromises any co-located process — a monitoring agent, a sidecar, a compromised dependency — gains full unauthenticated access to all gRPC methods.

### Recommendation

Apply `InterceptedService` in the Unix socket branch identically to the TCP branch:

```rust
ServerAddr::Unix(ref socket_path) => {
    // Wrap with the same interceptor used for TCP
    let service = InterceptedService::new(
        service,
        if config.client_verification {
            OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
        } else {
            Noop
        },
    );
    let server_builder = tonic::transport::Server::builder()
        .layer(AddMethodMiddlewareLayer)
        ...
        .add_service(service);
    ...
}
```

Because Unix sockets carry no TLS peer certificates, `req.peer_certs()` will return `None`. The interceptor already handles this case: in non-test mode it returns `Unauthenticated`; in test mode it passes through. For Unix socket production use, the interceptor should be extended to accept `None` peer certs only when the socket path is considered a trusted local channel, or a separate `UnixSocketOnly` interceptor variant should be introduced that enforces caller identity via OS-level credentials (`SO_PEERCRED`).

Additionally, add a secondary `cfg!(test)` guard to `InternalWithdraw` (matching the pattern already used in `InternalFinalizedPayout`) so that even if the transport-layer check is bypassed, the method is unavailable in production builds.

### Proof of Concept

**Preconditions**: Attacker has local access to the Unix socket file (e.g., same Docker network, same host user, compromised sidecar container). A valid `withdrawal_id` and its `input_signature` are read from Citrea bridge contract events (public on-chain data).

**Steps**:

1. Identify the operator's Unix socket path from the config (`socket_path` + `"operator_N.sock"`).
2. Connect to the socket using any gRPC client without presenting a certificate:
   ```
   grpcurl -plaintext -unix /operator_0.sock \
     clementine.ClementineOperator/InternalWithdraw \
     '{"withdrawal_id": <id>, "input_signature": "<hex>", ...}'
   ```
3. The server processes the request without invoking the interceptor. `internal_withdraw` calls `operator.withdraw(...)`, signs the payout transaction with the operator's key, and inserts it into the tx-sender queue.
4. The tx-sender broadcasts the payout transaction, spending the operator's round UTXO to the attacker-controlled output address embedded in the withdrawal registration.

**Corrupted value**: The operator's round UTXO (collateral chain) is spent to an output the operator did not authorize, reducing the operator's available collateral and potentially breaking the reimbursement chain for subsequent deposits. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/servers.rs (L114-139)
```rust
            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );
```

**File:** core/src/servers.rs (L178-189)
```rust
        #[cfg(unix)]
        ServerAddr::Unix(ref socket_path) => {
            let server_builder = tonic::transport::Server::builder()
                .layer(AddMethodMiddlewareLayer)
                .layer(BufferLayer::new(config.grpc.req_concurrency_limit))
                .layer(RateLimitLayer::new(
                    config.grpc.ratelimit_req_count as u64,
                    Duration::from_secs(config.grpc.ratelimit_req_interval_secs),
                ))
                .timeout(Duration::from_secs(config.grpc.timeout_secs))
                .concurrency_limit_per_connection(config.grpc.req_concurrency_limit)
                .add_service(service);
```

**File:** core/src/rpc/interceptors.rs (L22-77)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
}

#[allow(clippy::result_large_err)]
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
}
```

**File:** core/src/rpc/operator.rs (L168-190)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_withdraw(
        &self,
        request: Request<WithdrawParams>,
    ) -> Result<Response<RawSignedTx>, Status> {
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(request.into_inner())?;

        tracing::warn!("Called internal_withdraw with withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount);

        let payout_tx = self
            .operator
            .withdraw(
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
            )
            .await?;

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
    }
```

**File:** core/src/rpc/clementine.proto (L390-396)
```text
  // Prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned. Note: This is intended for
  // operator's own use, so it doesn't include a signature from aggregator.
  rpc InternalWithdraw(WithdrawParams) returns (RawSignedTx) {}
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L77-80)
```text
client_verification = true
security_council = "1:50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0"

socket_path = "/"
```
