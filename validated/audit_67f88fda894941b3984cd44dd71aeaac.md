### Title
Unauthenticated tx-sender JSON-RPC Server Allows Any Network-Reachable Party to Insert or Cancel Bridge Transactions — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server is started with no authentication layer of any kind. Any party that can reach the bound address can call `send_tx` (and `send_citrea_tx`) to insert arbitrary transactions into the broadcast queue, or to cancel existing bridge transactions already queued, by supplying `cancel_outpoints` / `cancel_txids` in the request. This bypasses the mTLS trust boundary that protects every other bridge RPC surface and can cause permanent loss of operator collateral or permanent lock of bridged BTC by suppressing legitimate kickoff, payout, or reimbursement transactions.

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain HTTP/1.1 JSON-RPC server using `jsonrpsee::server::ServerBuilder::default()` with no TLS, no client-certificate check, and no token/API-key middleware:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
    ...
``` [1](#0-0) 

The `send_tx` handler then calls `TxSenderClient::insert_try_to_send` directly, accepting caller-supplied `cancel_outpoints`, `cancel_txids`, `activate_txids`, and `activate_outpoints`:

```rust
client.insert_try_to_send(
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
``` [2](#0-1) 

The bind address is controlled by `TxSenderJsonRpcConfig::bind`, documented as "Restricted to 127.0.0.1 or 0.0.0.0" — but this is a comment only; no code validates or enforces the restriction: [3](#0-2) 

A dedicated `tx-sender-jsonrpc-client` crate exists, confirming this interface is part of the production surface, not a test helper:



Contrast this with every other bridge RPC surface. The gRPC servers for Operator and Verifier wrap their service with `InterceptedService::new(service, OnlyAggregatorAndSelf { … })` and enforce mTLS client-certificate pinning: [4](#0-3) 

The interceptor rejects any caller whose leaf certificate is not the pinned aggregator cert or the entity's own cert: [5](#0-4) 

The tx-sender JSON-RPC server has none of this.

### Impact Explanation

`insert_try_to_send` is the single write path into the tx-sender broadcast queue. The `cancel_outpoints` and `cancel_txids` fields allow the caller to atomically dequeue and suppress any existing entry that spends a given outpoint or matches a given txid. An unauthenticated attacker who can reach the JSON-RPC port can:

1. **Cancel kickoff transactions** — preventing the operator from entering the reimbursement window, causing the operator to forfeit collateral.
2. **Cancel payout transactions** — preventing withdrawal completion, permanently locking bridged BTC in the vault.
3. **Cancel reimbursement transactions** — causing the operator to lose the BTC they already paid out.
4. **Insert a conflicting transaction** — by supplying a low-fee transaction that spends the same UTXO as a legitimate bridge transaction, forcing the legitimate transaction to be treated as double-spent.

All of these outcomes fall within the allowed impact gate: permanent lock or loss of bridged BTC and slashable exposure of operator collateral.

### Likelihood Explanation

The server is started whenever `TxSenderConfig::jsonrpc` is `Some(...)`. The config field allows `bind = "0.0.0.0"` with no code-level enforcement of the localhost-only intent. In a Docker or cloud deployment where the tx-sender container's port is inadvertently exposed (e.g., via a misconfigured port mapping or a shared network namespace), any host on the same network segment can reach it with no credentials. The attack requires only a standard HTTP POST with a JSON body — no cryptographic material needed.

### Recommendation

1. **Add authentication to the JSON-RPC server.** At minimum, require a shared secret (Bearer token or HMAC-signed request) validated in a tower middleware layer before any handler is invoked.
2. **Enforce the localhost-only bind restriction in code.** Parse the `bind` field and return a startup error if it is not `127.0.0.1` or `::1`.
3. **Restrict `cancel_outpoints`/`cancel_txids` to callers that own the queued entry.** The DB schema should record which actor inserted each entry and reject cancellation requests from a different caller identity.
4. **Consider replacing the unauthenticated JSON-RPC interface with a Unix-domain socket** (filesystem-permission-gated) or a mTLS-protected gRPC channel consistent with the rest of the bridge.

### Proof of Concept

Assuming the tx-sender JSON-RPC server is bound to `0.0.0.0:PORT` (permitted by config):

```bash
# Attacker cancels a legitimate kickoff transaction already in the queue
# by supplying its outpoint in cancel_outpoints.
curl -s -X POST http://<tx-sender-host>:<PORT> \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_tx",
    "params": [{
      "signed_tx_hex": "<any_syntactically_valid_tx_hex>",
      "tx_metadata": null,
      "fee_paying_type": "CPFP",
      "rbf_signing_info": null,
      "cancel_outpoints": [
        { "txid": "<kickoff_txid>", "vout": 0 }
      ],
      "cancel_txids": [],
      "activate_txids": [],
      "activate_outpoints": []
    }]
  }'
```

No certificate, no token, no prior knowledge of any bridge secret is required. The kickoff transaction is removed from the queue; the operator misses the reimbursement window and loses collateral.

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-51)
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

```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L58-88)
```rust
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
```

**File:** crates/clementine-tx-sender/src/config.rs (L29-35)
```rust
#[derive(Clone, Debug)]
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
```

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

**File:** core/src/rpc/interceptors.rs (L36-77)
```rust
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
