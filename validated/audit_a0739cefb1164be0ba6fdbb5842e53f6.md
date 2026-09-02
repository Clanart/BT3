This confirms the design is explicitly documented: "The aggregator does not enforce client certificates but does use TLS for encryption" (docs/usage.md:203). Even with `client_verification = true`, the interceptor for the aggregator still relies on the same `only_aggregator_and_self` mechanism used for verifier/operator, but the aggregator's own internal methods are guarded by this mechanism the same way as other entities—the docs statement refers to the aggregator not restricting who calls it as "aggregator" (since it has no "aggregator" above it), not that internal methods aren't checked when `client_verification=true`.

Given the question explicitly sets `client_verification=false` as a precondition, this is a valid, chosen deployment configuration, not something forced by the code. All shipped/example configs (`core/src/test/data/bridge_config.toml:73`, `scripts/docker/configs/testnet4/bridge_config.toml:77`, `.env.example:82`) set `client_verification = true`, and the code default (`core/src/config/mod.rs:486`) is also `true`. So `client_verification=false` is an atypical/misconfiguration choice by the operator deploying the aggregator, not the documented or default behavior.### Title
Unauthenticated `InternalSendTx` broadcast via aggregator `Noop` interceptor when `client_verification=false` - (`core/src/rpc/interceptors.rs`, `core/src/rpc/aggregator.rs`)

### Summary
The `Internal*`-prefixed gRPC methods are documented and designed to be reachable "only by the entity itself" via the `only_aggregator_and_self` check inside the `OnlyAggregatorAndSelf` interceptor variant. When an operator deploys the aggregator with `client_verification=false`, `create_grpc_server` installs `Interceptors::Noop`, which returns `Ok(req)` unconditionally for every request, so `is_internal`/leaf-cert equality is never evaluated. Combined with the `automation` feature, this makes `ClementineAggregator::internal_send_tx` a fully unauthenticated, state-changing/broadcasting RPC.

### Finding Description
The broken binding, stated as an equality: `leaf_cert == our_cert` must hold for any caller of an `Internal*` method (`core/src/rpc/interceptors.rs:62-69`). With `Interceptors::Noop`, this equality is never computed — `Interceptors::call` for `Noop` just does `Ok(req)` (`core/src/rpc/interceptors.rs:30`), so the check "caller == self" is vacuously bypassed for every request, including ones where caller ≠ self.

`create_grpc_server` selects `Noop` instead of `OnlyAggregatorAndSelf` whenever `config.client_verification` is `false`: [1](#0-0) 

`create_aggregator_grpc_server` passes the aggregator's `BridgeConfig` straight through to `create_grpc_server` with no additional gate beyond a warning log when verification is enabled: [2](#0-1) 

The gRPC service definition marks `InternalSendTx` as an `Internal*` method (matched by `is_internal`'s `starts_with(b"Internal")` check): [3](#0-2) 

The handler itself performs no additional caller check — it trusts the interceptor layer entirely and, when compiled with `automation`, deserializes the attacker-supplied `raw_tx` and enqueues it into the tx sender for broadcast/fee-bumping: [4](#0-3) 

Exploit flow: attacker connects to the aggregator's TCP gRPC port (TLS still required for transport, but no client certificate check occurs because `client_verification=false` ⇒ `Noop`), sends `InternalSendTx(SendTxRequest{raw_tx: <attacker's own valid signed tx>, fee_type})`. The Noop interceptor passes the request through unmodified; `internal_send_tx` decodes it and calls `tx_sender.insert_try_to_send(...)`, queuing the transaction for the tx-sender's CPFP/RBF broadcast loop, which — per the RBF path — can use the aggregator's own wallet (`fund_raw_transaction`) to add fee-paying inputs/outputs: [5](#0-4) 

The intended guard (`only_aggregator_and_self`) exists and works correctly when `client_verification=true` (the default: `core/src/config/mod.rs:486`, and used in every shipped sample config), but nothing in the code prevents an operator from disabling it entirely on the aggregator, and the aggregator applies the exact same interceptor-selection logic as the verifier/operator despite `docs/usage.md` stating the aggregator does not enforce client certificates at all.

### Impact Explanation
This matches the High-severity category "an unauthenticated state-changing or broadcasting call." Any unprivileged party who can reach the aggregator's gRPC port (a capability explicitly granted to the attacker per the rules) can, under this configuration, get the aggregator's automation/tx-sender infrastructure to accept and attempt to broadcast an arbitrary transaction, and depending on the `fee_type`/RBF path, cause the aggregator's own wallet to spend funds subsidizing/funding an attacker-chosen transaction. This is repeatable per call and is not scoped to a single deposit/operator — it is a blanket bypass of the `Internal*` method authorization for the entire aggregator surface (also affects `InternalGetEmergencyStopTx`, etc.), not just `InternalSendTx`.

### Likelihood Explanation
Exploitability strictly requires `client_verification=false` on the aggregator and the `automation` feature compiled in. Every shipped example config (`core/src/test/data/bridge_config.toml:73`, `scripts/docker/configs/testnet4/bridge_config.toml:77`, `.env.example:82`) and the hardcoded `Default` for `BridgeConfig` (`core/src/config/mod.rs:486`) set `client_verification = true`, under which the `OnlyAggregatorAndSelf` interceptor is active and the leaf-cert equality check correctly rejects non-self callers, closing this path. The vulnerability is therefore conditional on a non-default deployment choice; likelihood is low under documented/default settings but the code provides no independent safeguard (e.g., no in-handler self-authentication) if an operator does disable `client_verification`, so the blast radius under that misconfiguration is total (any network-reachable caller, zero cost beyond a valid signed transaction and a TCP/TLS connection).

### Recommendation
Do not let `client_verification=false` silently disable authorization for `Internal*` methods. Either (a) always enforce the self-only check for methods whose `grpc-method` starts with `Internal`, independent of `client_verification` (i.e., keep a minimal interceptor active even in "Noop" mode that still checks `is_internal` requests against `our_cert`), or (b) add an explicit in-handler check inside `internal_send_tx` (and other `Internal*` handlers) that fails closed unless the caller can be cryptographically proven to be the aggregator itself, so that disabling public-facing client verification cannot also disable internal-method self-authentication.

### Proof of Concept
```
cargo test -p clementine-core --features automation test_internal_send_tx_unauthenticated_with_noop_interceptor
```
Test plan:
1. Build a `BridgeConfig` with `client_verification = false` and start `create_aggregator_grpc_server` (or `create_aggregator_unix_server` in test mode).
2. Connect a gRPC client using an *arbitrary* client certificate (not `client_cert_path`/`aggregator_cert_path`), or no client cert at all where permitted by the TLS config (client_ca_root is not set when `client_verification=false`).
3. Craft a benign, self-signed valid Bitcoin transaction (`raw_tx`) and call `InternalSendTx(SendTxRequest{raw_tx, fee_type: Cpfp})`.
4. Assert both sides of the binding:
   - Before: `leaf_cert (arbitrary/unauthenticated) != our_cert (aggregator's client_cert_path)`.
   - After: assert the call returns `Ok(Empty {})` (not `Status::unauthenticated`), and assert the transaction row exists in the tx_sender DB via `debug_tx`/`get_try_to_send_tx`, proving the tx was queued for broadcast despite the caller not being the aggregator's own identity.
5. As a control, repeat with `client_verification = true` and confirm the same call returns `Status::unauthenticated("Unauthorized call to internal method (not self)")`, demonstrating the interceptor is the operative guard that Noop removes.

### Citations

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

**File:** core/src/rpc/clementine.proto (L777-779)
```text

  // Send a pre-signed tx to the network
  rpc InternalSendTx(SendTxRequest) returns (Empty) {}
```

**File:** core/src/rpc/aggregator.rs (L1269-1312)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_send_tx(
        &self,
        request: Request<clementine::SendTxRequest>,
    ) -> Result<Response<Empty>, Status> {
        #[cfg(not(feature = "automation"))]
        {
            Err(Status::unimplemented("Automation is not enabled"))
        }
        #[cfg(feature = "automation")]
        {
            let send_tx_req = request.into_inner();
            let fee_type = send_tx_req.fee_type();
            let signed_tx: bitcoin::Transaction = send_tx_req
                .raw_tx
                .ok_or(Status::invalid_argument("Missing raw_tx"))?
                .try_into()?;
            tracing::warn!(
                "Internal send tx rpc called with feetype: {:?}, tx hex: {}",
                fee_type,
                bitcoin::consensus::encode::serialize_hex(&signed_tx)
            );

            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    None,
                    &signed_tx,
                    fee_type.try_into()?,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;
            Ok(Response::new(Empty {}))
        }
    }
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L532-548)
```rust
    /// Sends or bumps a transaction using the Replace-By-Fee (RBF) strategy.
    ///
    /// It interacts with the database to track the latest RBF attempt (`last_rbf_txid`).
    ///
    /// # Logic:
    /// 1.  **Check for Existing RBF Tx:** Retrieves RBF txids for the `try_to_send_id` and
    ///     selects the most recent one still in the mempool.
    /// 2.  **Bump Existing Tx:** If a mempool tx exists, it calls `rpc.psbt_bump_fee`.
    ///     - This internally uses the Bitcoin Core `psbtbumpfee` RPC.
    ///     - We then sign the inputs that we can using our Actor and have the wallet sign the rest.
    ///
    /// 3.  **Send Initial RBF Tx:** If no RBF tx is found in the mempool:
    ///     - It uses `fund_raw_transaction` RPC to let the wallet add (potentially) inputs,
    ///       outputs, set the fee according to `fee_rate`, and mark the transaction as replaceable.
    ///     - Uses `sign_raw_transaction_with_wallet` RPC to sign the funded transaction.
    ///     - Uses `send_raw_transaction` RPC to broadcast the initial RBF transaction.
    ///     - Saves the resulting `txid` to the database as the `last_rbf_txid`.
```
