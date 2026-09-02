### Title
Standalone tx-sender JSON-RPC server exposes unauthenticated `send_tx`/`send_citrea_tx` broadcasting methods - (File: crates/clementine-tx-sender/src/jsonrpc/server.rs)

### Summary
`clementine-tx-sender`'s JSON-RPC interface registers `send_tx` (and, with the `citrea` feature, `send_citrea_tx`) on a plain `jsonrpsee` HTTP server with no authentication layer at all, in contrast to every other state-changing entry point in the codebase (operator/verifier/aggregator gRPC), which is gated by mTLS and an `Interceptor` that restricts access to the aggregator or the entity itself.

### Finding Description
Every state-changing gRPC method exposed by the verifier and operator servers is wrapped in an `InterceptedService` that enforces `OnlyAggregatorAndSelf`, checking the peer's TLS leaf certificate against the configured aggregator/self certificates before allowing the call [1](#0-0) , and the interceptor itself further restricts any RPC method whose name starts with `Internal` to only the entity's own certificate [2](#0-1) .

The `clementine-tx-sender` crate, however, also exposes a JSON-RPC server (enabled by the `json-rpc` feature) that registers `send_tx` directly on an unauthenticated `jsonrpsee::server::Server` bound to a configurable TCP address, with no TLS, no certificate check, and no caller-identity verification whatsoever: `start_jsonrpc_server` builds the server and registers `send_tx`, which deserializes an attacker-supplied `signed_tx_hex` and calls `insert_try_to_send` to enqueue it for broadcast [3](#0-2) . The `send_citrea_tx` method, when the `citrea` feature is enabled, is registered the same way with no auth [4](#0-3) .

This mirrors the reported bug class in the external report: a lower-level, state-changing/broadcasting entry point (`DecentEthRouter::bridgeWithPayload`) lacked the access control that its intended caller (`DecentBridgeAdapter`) was supposed to enforce, letting any unprivileged caller reach it directly. Here, the intended callers of tx-broadcasting functionality (aggregator/operator/verifier, authenticated via mTLS) are bypassed entirely because this parallel JSON-RPC entry point performs the equivalent state-changing action (queuing/broadcasting a transaction) without any of the authentication machinery used elsewhere in the repository.

### Impact Explanation
This matches the "High" impact category of "an unauthenticated state-changing or broadcasting call." Any network-reachable, unauthenticated party can call `send_tx` to insert a transaction into the tx-sender's send queue, from where it will eventually be broadcast/fee-bumped by the tx-sender's background loop. This breaks the intended authorization boundary that the rest of the system enforces via mTLS (`core/src/servers.rs`, `core/src/rpc/interceptors.rs`), i.e., the binding "caller reaching a broadcasting method == the aggregator/operator itself" is violated.

### Likelihood Explanation
Likelihood depends on whether the JSON-RPC listener is bound to a network-reachable interface in a given deployment. The feature is compiled into the standalone `clementine-tx-sender` binary and configured via `TX_SENDER_JSONRPC_BIND`/`TX_SENDER_JSONRPC_PORT` [5](#0-4) , and is registered without any authentication code path in the source itself — no configuration option exists in `server.rs` to add TLS or a client check. Since the vulnerability is present unconditionally whenever the `json-rpc` feature/service is run (there is no code path providing auth), likelihood is tied to whether the operator binds it to a non-localhost address, which is a deployment/operational decision rather than a code defect that always requires misconfiguration to trigger a security-relevant exposure.

### Recommendation
Add an authentication layer to the JSON-RPC server (e.g., a shared bearer token/API key, IP allowlisting, or wrapping it in the same mTLS-based interceptor pattern used by `core/src/servers.rs` and `core/src/rpc/interceptors.rs`) before allowing `send_tx`/`send_citrea_tx` to enqueue or broadcast transactions.

### Proof of Concept
As documented in the crate's own smoke-test script, any client with network access to the JSON-RPC port can broadcast a transaction with no authentication: [6](#0-5) 
This is functionally identical to calling the RPC via `curl` directly against `send_tx` with an arbitrary signed transaction; no credential or certificate is required to reach `start_jsonrpc_server`'s registered handler [7](#0-6) .

Note: I was not able to fully verify from the indexed code whether the production/documented deployment topology (e.g., Docker/Helm configs) always restricts this JSON-RPC listener to a private/loopback network, since only `Dockerfile`/`run.sh`/`config.rs` references were visible and full deployment manifests were out of scope (`devops/**`, `*.toml` excluded per the rules). If deployment configuration always binds this to localhost-only, the practical exposure would be much lower than described above.

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

**File:** core/src/rpc/interceptors.rs (L12-76)
```rust
fn is_internal(req: &Request<()>) -> bool {
    // This normally doesn't exist but we add it in the AddMethodMiddleware
    let Some(path) = req.metadata().get("grpc-method") else {
        // No grpc method? this should not happen
        tracing::error!("Missing grpc-method header in request");
        return false;
    };
    path.as_bytes().starts_with(b"Internal")
}

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
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-89)
```rust
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let local_addr = server
        .local_addr()
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let mut module = RpcModule::new(tx_sender_client.clone());
    module
        .register_async_method("send_tx", |params, client, _| async move {
            let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;

            let raw_tx = hex::decode(&req.signed_tx_hex).map_err(jsonrpc_err)?;
            let signed_tx: Transaction = consensus::deserialize(&raw_tx).map_err(jsonrpc_err)?;

            let mut dbtx = client.db.begin_transaction().await.map_err(jsonrpc_err)?;

            let try_to_send_id = client
                .insert_try_to_send(
                    &mut dbtx,
                    req.tx_metadata,
                    &signed_tx,
                    req.fee_paying_type,
                    req.rbf_signing_info,
                    &req.cancel_outpoints,
                    &req.cancel_txids,
                    &req.activate_txids,
                    &req.activate_outpoints,
                )
                .await
                .map_err(jsonrpc_err)?;

            client
                .db
                .commit_transaction(dbtx)
                .await
                .map_err(jsonrpc_err)?;

            Ok::<u32, ErrorObjectOwned>(try_to_send_id)
        })
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L91-106)
```rust
    // Citrea-specific RPCs.
    #[cfg(feature = "citrea")]
    {
        module
            .register_async_method("send_citrea_tx", |params, client, _| async move {
                let req: InsertCitreaRawTxParams = params.one().map_err(jsonrpc_err)?;

                let insertion_id = client
                    .send_citrea_tx(req.citrea_tx_request)
                    .await
                    .map_err(jsonrpc_err)?;

                Ok::<i64, ErrorObjectOwned>(insertion_id)
            })
            .map_err(|e| BridgeError::Eyre(e.into()))?;
    }
```

**File:** crates/clementine-tx-sender/run.sh (L35-39)
```shellscript
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
export TX_SENDER_POLL_DELAY_MS="${TX_SENDER_POLL_DELAY_MS:-500}"
export TX_SENDER_FINALITY_DEPTH="${TX_SENDER_FINALITY_DEPTH:-1}"
export TX_SENDER_INCLUDE_UNSAFE="${TX_SENDER_INCLUDE_UNSAFE:-true}"
```

**File:** crates/clementine-tx-sender/run.sh (L142-177)
```shellscript
send_via_jsonrpc() {
  local url="$1"
  local raw_tx_hex="$2"

  local req
  req="$(RAW_TX_HEX="${raw_tx_hex}" python3 - <<'PY'
import json, os
raw_tx_hex = os.environ["RAW_TX_HEX"]
payload = {
  "jsonrpc":"2.0",
  "id": 1,
  "method": "send_tx",
  "params": [{
    "tx_metadata": None,
    "signed_tx_hex": raw_tx_hex,
    "fee_paying_type": "NoFunding",
    "rbf_signing_info": None,
    "cancel_outpoints": [],
    "cancel_txids": [],
    "activate_txids": [],
    "activate_outpoints": [],
  }],
}
print(json.dumps(payload))
PY
)"

  curl -sS \
    -H 'content-type: application/json' \
    --data "${req}" \
    "${url}" \
    | python3 -c 'import sys, json; r=json.load(sys.stdin);
if "error" in r:
  raise SystemExit("JSON-RPC error: %s" % r["error"])
print(r["result"])'
}
```
