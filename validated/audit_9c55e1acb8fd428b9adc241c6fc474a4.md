Based on the evidence gathered, this is a real, demonstrable vulnerability in the interceptor design, not a false positive.

### Title
Unauthenticated bypass of internal-only RPC gating exposes emergency-stop transaction ciphertexts when `client_verification=false` - ([File: core/src/rpc/interceptors.rs])

### Summary
`ClementineAggregator::internal_get_emergency_stop_tx` is intended to be reachable only by the aggregator itself, per the `Internal*` naming convention enforced in `only_aggregator_and_self`. When `config.client_verification` is `false`, `Interceptors::Noop` is installed instead, which unconditionally returns `Ok(req)` for every request, so the `is_internal` self-check never executes and any unauthenticated caller can invoke the handler.

### Finding Description
The intended binding is: `is_internal(req) && leaf_cert == our_cert` must hold for any RPC whose path starts with `Internal` to succeed, as implemented in `only_aggregator_and_self` [1](#0-0) . This check only runs inside the `OnlyAggregatorAndSelf` variant of `Interceptors`; the `Noop` variant simply returns `Ok(req)` regardless of the method path or certificate [2](#0-1) .

Which interceptor gets installed is decided in `create_grpc_server` based solely on `config.client_verification`: if `true`, `OnlyAggregatorAndSelf` is built from the loaded certs; if `false`, `Noop` is used and no certificate loading or TLS client-CA configuration happens at all [3](#0-2) . This applies identically to the aggregator's gRPC server construction in `create_aggregator_grpc_server`, which only logs a warning when `client_verification` is enabled but performs no additional gating when it is disabled [4](#0-3) .

The handler itself, `internal_get_emergency_stop_tx`, fetches encrypted emergency-stop transactions by move-to-vault txid and returns them to the caller with no further authorization check inside the method body — the only prior control layer is the interceptor. The underlying data was populated earlier by `verify_and_save_emergency_stop_sigs`, which calls `encrypt_bytes` against the security council's X25519 public key and stores the ciphertext via `insert_signed_emergency_stop_tx_if_not_exists` [5](#0-4) .

Exploit flow: an attacker connects to the aggregator's public gRPC port with `client_verification=false` (Noop installed), sends `InternalGetEmergencyStopTx { txids: [<any known move-to-vault txid>] }` with no TLS client certificate, `Noop::call` accepts the request unconditionally, and the handler returns the stored `encrypted_emergency_stop_txs` ciphertexts for that txid. No self-check, no certificate check, no authentication of any kind is applied.

### Impact Explanation
The attacker obtains ciphertexts of emergency-stop transactions for any move-to-vault txid they can observe on-chain or guess. Because `encrypt_bytes` encrypts to the security council's X25519 key, plaintext transaction contents are not directly recovered without the council's private key, so no BTC moves and no signature is directly leaked. This does not meet any of the Critical impact categories (no value moves, no reimbursement mis-credit, no permanent freeze, no forged/blocked claim, no exposed N-of-N partial signature or secnonce). It does match the High category "premature disclosure of a protocol commitment": an unauthenticated caller reaches a state-reading, internal-only-gated RPC and obtains encrypted commitments (the emergency-stop tx ciphertexts) that were intended to remain internal to the aggregator/self only. The blast radius is every deposit whose emergency-stop signature has been finalized and stored, and it is repeatable for any txid the attacker can enumerate.

### Likelihood Explanation
This requires the specific deployment configuration `client_verification=false`, which is an explicit, documented (non-default in production configs, but present as a toggle) configuration option in `BridgeConfig`; whether it is actually deployed with this value in production is not verifiable from the code alone but the toggle and code path clearly exist and are exercised by `create_grpc_server`. Given that configuration, the attack costs nothing (no BTC, no fees) and is trivially repeatable — a single unauthenticated gRPC call per known txid, requiring only that the caller already know or observe a move-to-vault txid on-chain.

### Recommendation
Do not rely on TLS/certificate-based interceptor logic alone for gating `Internal*` methods; add a check that is enforced independently of the `client_verification` config flag, e.g., always deny `Internal*`-prefixed methods when the transport does not present a matching self certificate, or refuse to instantiate `Interceptors::Noop` for the aggregator service (or reject any `is_internal` request outright when `Noop` is used) so that `Internal*` RPCs are unreachable regardless of the `client_verification` setting.

### Proof of Concept
`cargo test` plan:
1. Build an aggregator test server via `create_aggregator_unix_server`/`create_grpc_server` with `config.client_verification = false` (forces `Noop`).
2. Populate the DB with a finalized emergency-stop signature for a known `move_to_vault_txid` via `insert_signed_emergency_stop_tx_if_not_exists` (as done inside `verify_and_save_emergency_stop_sigs`).
3. Connect a plain gRPC client with no TLS client identity and call `internal_get_emergency_stop_tx(GetEmergencyStopTxRequest{txids: [move_to_vault_txid]})`.
4. Assert the response is `Ok(..)` with `encrypted_emergency_stop_txs` non-empty and matching the stored ciphertext.
5. Repeat with `config.client_verification = true` and a non-self client certificate; assert the call returns `Status::unauthenticated` from `only_aggregator_and_self`, demonstrating the divergence caused solely by the `client_verification` flag choosing `Noop` vs `OnlyAggregatorAndSelf`.

### Citations

**File:** core/src/rpc/interceptors.rs (L22-33)
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
```

**File:** core/src/rpc/interceptors.rs (L62-70)
```rust
    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
```

**File:** core/src/servers.rs (L106-139)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };

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

**File:** core/src/servers.rs (L293-317)
```rust
pub async fn create_aggregator_grpc_server(
    config: BridgeConfig,
) -> Result<(std::net::SocketAddr, oneshot::Sender<()>), BridgeError> {
    let addr: std::net::SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .wrap_err("Failed to parse address")?;
    let aggregator_server = AggregatorServer::new(config.clone()).await?;
    aggregator_server.start_background_tasks().await?;

    let svc = ClementineAggregatorServer::new(aggregator_server)
        .max_encoding_message_size(config.grpc.max_message_size)
        .max_decoding_message_size(config.grpc.max_message_size);

    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }

    let (server_addr, shutdown_tx) =
        create_grpc_server(addr.into(), svc, "Aggregator", &config).await?;

    match server_addr {
        ServerAddr::Tcp(socket_addr) => Ok((socket_addr, shutdown_tx)),
        _ => Err(BridgeError::ConfigError("Expected TCP address".into())),
    }
}
```

**File:** core/src/rpc/aggregator.rs (L907-922)
```rust
        let emergency_stop_pubkey = self
            .config
            .emergency_stop_encryption_public_key
            .ok_or_else(|| eyre::eyre!("Emergency stop encryption public key is not set"))?;
        let encrypted_emergency_stop_tx = crate::encryption::encrypt_bytes(
            emergency_stop_pubkey,
            &bitcoin::consensus::serialize(&emergency_stop_tx),
        )?;

        self.db
            .insert_signed_emergency_stop_tx_if_not_exists(
                None,
                move_to_vault_txid,
                &encrypted_emergency_stop_tx,
            )
            .await?;
```
