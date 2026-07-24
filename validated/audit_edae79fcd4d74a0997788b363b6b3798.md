### Title
Unauthenticated TxSender JSON-RPC `send_tx` Endpoint Allows Arbitrary Transaction Queuing and Fee-UTXO Draining — (File: `crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The `clementine-tx-sender` JSON-RPC server exposes `send_tx` (and `send_citrea_tx`) with zero authentication. Any caller that can reach the bound address can queue arbitrary Bitcoin transactions for broadcasting and force the TxSender to spend its own fee-paying UTXOs on CPFP child transactions, draining tx-sender-managed balances and potentially starving legitimate bridge transactions of fee-bumping capacity.

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain HTTP JSON-RPC server using `jsonrpsee::server::ServerBuilder::default()` and registers `send_tx` and `send_citrea_tx` with no authentication layer, no API key, no IP allowlist, and no TLS:

```rust
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)          // ← plain HTTP, no TLS, no auth
        .await
        ...
    module
        .register_async_method("send_tx", |params, client, _| async move {
            // ← no caller identity check
            let req: InsertTryToSendParams = params.one()...;
            client.insert_try_to_send(...).await...
        })
``` [1](#0-0) 

The bind address is configurable via `TX_SENDER_JSONRPC_BIND`. The config parser explicitly accepts `0.0.0.0` as a valid value:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(BridgeError::EnvVarMalformed(...));
}
``` [2](#0-1) 

When `TX_SENDER_JSONRPC_BIND=0.0.0.0` is set (a value the code explicitly permits), the server is reachable from any network peer. There is no code path that would authenticate callers regardless of the bind address — the authentication mechanism simply does not exist in `start_jsonrpc_server`.

This contrasts sharply with the gRPC servers for Operator and Verifier, which enforce mTLS with the `OnlyAggregatorAndSelf` interceptor: [3](#0-2) [4](#0-3) 

The `insert_try_to_send` call accepts:
- `fee_paying_type` — controls whether the TxSender creates a CPFP child transaction spending from its own fee-paying UTXOs
- `cancel_txids` / `cancel_outpoints` — marks other queued transactions as cancelled when the submitted transaction confirms
- `activate_txids` / `activate_outpoints` — creates false dependency chains in the TxSender state machine [5](#0-4) 

---

### Impact Explanation

**Fee-UTXO draining (direct, no confirmation required):** An attacker submits a transaction with `fee_paying_type: CPFP`. The TxSender immediately creates a CPFP child transaction signed by its own key and spending from its own fee-paying UTXOs, then broadcasts it. Repeating this drains the TxSender's entire fee-paying UTXO set. Once exhausted, the TxSender cannot fee-bump any legitimate bridge transaction (kickoffs, challenges, payouts, reimbursements), causing liveness failure.

**Queue cancellation (requires attacker transaction to confirm):** An attacker submits a transaction with `cancel_txids` pointing to legitimate bridge transaction IDs in the TxSender queue. If the attacker's transaction confirms on Bitcoin, those bridge transactions are marked cancelled and will not be broadcast. This can prevent challenge transactions from being submitted within their timelock window, allowing a malicious operator to steal bridged BTC.

**Scope match:** Both impacts fall within the allowed gate — "loss of tx-sender-managed balances" and "unauthorized state transition in challenge/payout flow that breaks bridge liveness with material fund impact."

---

### Likelihood Explanation

The default bind address is `127.0.0.1`, which limits exposure to local processes. However:
1. The code explicitly permits `0.0.0.0` as a valid value with no compensating authentication.
2. The `run.sh` smoke-test script exports `TX_SENDER_JSONRPC_BIND` as an overridable environment variable, normalizing the pattern of changing it.
3. Operators running the TxSender as a standalone service (the binary in `crates/clementine-tx-sender/src/main.rs`) may expose it to a wider network for integration with other bridge components.
4. There is no documentation warning that `0.0.0.0` is unsafe without a firewall. [6](#0-5) [7](#0-6) 

---

### Recommendation

1. **Add authentication to `start_jsonrpc_server`**: Implement a shared-secret bearer token or HMAC-based request authentication checked in every registered method handler before any DB or broadcast operation.
2. **Reject `0.0.0.0` without explicit authentication config**: If `TX_SENDER_JSONRPC_BIND=0.0.0.0` is set and no authentication secret is configured, return a startup error.
3. **Restrict `cancel_txids`/`cancel_outpoints` to internal callers only**: These parameters can manipulate the TxSender state machine and should not be accepted from the JSON-RPC interface at all, or should require a higher privilege level.
4. **Document the security boundary**: Clearly state that the JSON-RPC interface must be firewalled to trusted callers only.

---

### Proof of Concept

Assuming `TX_SENDER_JSONRPC_BIND=0.0.0.0` and `TX_SENDER_JSONRPC_PORT=3030`:

```bash
# Step 1: Drain fee-paying UTXOs by requesting CPFP fee bumping for a dummy tx
# (attacker supplies any syntactically valid signed tx hex)
curl -s -X POST http://<target>:3030 \
  -H 'content-type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_tx",
    "params": [{
      "tx_metadata": null,
      "signed_tx_hex": "<attacker_signed_tx_hex>",
      "fee_paying_type": "CPFP",
      "rbf_signing_info": null,
      "cancel_outpoints": [],
      "cancel_txids": ["<legitimate_bridge_kickoff_txid>"],
      "activate_txids": [],
      "activate_outpoints": []
    }]
  }'
# TxSender immediately creates a CPFP child spending its own UTXOs.
# If attacker's tx confirms, the kickoff tx is cancelled from the queue.
```

No credentials, no TLS, no rate-limit bypass required. The only prerequisite is network reachability to the JSON-RPC port. [8](#0-7) [2](#0-1)

### Citations

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

**File:** crates/clementine-tx-sender/src/config.rs (L197-212)
```rust
        #[cfg(feature = "json-rpc")]
        let jsonrpc = {
            let port = env_parse_optional::<u16>("TX_SENDER_JSONRPC_PORT")?;
            port.map(|port| {
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
            })
            .transpose()?
        };
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

**File:** crates/clementine-tx-sender/src/client.rs (L59-70)
```rust
    pub async fn insert_try_to_send(
        &self,
        dbtx: &mut crate::TxSenderTransaction,
        tx_metadata: Option<TxMetadata>,
        signed_tx: &Transaction,
        fee_paying_type: FeePayingType,
        rbf_signing_info: Option<RbfSigningInfo>,
        cancel_outpoints: &[OutPoint],
        cancel_txids: &[Txid],
        activate_txids: &[ActivatedWithTxid],
        activate_outpoints: &[ActivatedWithOutpoint],
    ) -> Result<u32, BridgeError> {
```

**File:** crates/clementine-tx-sender/run.sh (L35-37)
```shellscript
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
export TX_SENDER_POLL_DELAY_MS="${TX_SENDER_POLL_DELAY_MS:-500}"
```

**File:** crates/clementine-tx-sender/src/main.rs (L1-29)
```rust
#[cfg(feature = "json-rpc")]
#[tokio::main]
async fn main() -> Result<(), eyre::Report> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config: clementine_tx_sender::config::TxSenderConfig =
        clementine_tx_sender::config::TxSenderConfig::from_env()?;

    if config.jsonrpc.is_none() {
        return Err(eyre::eyre!(
            "TX_SENDER_JSONRPC_PORT must be set to start the JSON-RPC server"
        ));
    }

    let db = clementine_tx_sender::TxSenderDb::connect(&config.postgres).await?;
    db.run_migrations().await?;
    db.pool().close().await;

    let handle = clementine_tx_sender::task::spawn_txsender_loop(config);

    // Wait until Ctrl-C, then abort the background loop.
    tokio::signal::ctrl_c().await?;
    tracing::info!("Received Ctrl-C, shutting down txsender");
    handle.abort();
    let _ = handle.await;
    Ok(())
}
```
