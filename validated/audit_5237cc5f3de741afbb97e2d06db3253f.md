### Title
Unauthenticated tx-sender JSON-RPC `send_tx` endpoint allows any network-reachable caller to insert arbitrary transactions into the bridge's sending queue — (`File: crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server exposes `send_tx` (and `send_citrea_tx`) over plain HTTP with no authentication, no TLS, and no caller verification. When the service is bound to `0.0.0.0` — an explicitly supported and documented configuration — any network-reachable party can enqueue arbitrary transactions, manipulate activation/cancellation conditions for newly inserted entries, and flood the queue. Because the tx-sender is responsible for broadcasting time-sensitive bridge transactions (challenge responses, kickoff, payout, disprove-timeout, etc.), a sustained queue-flooding or conflicting-transaction attack can prevent those transactions from being submitted within their Bitcoin timelock windows, causing operators to lose collateral or allowing fraudulent withdrawals to go unchallenged.

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain `jsonrpsee` HTTP server with no middleware for authentication:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)   // no TLS, no auth
    .await ...;
``` [1](#0-0) 

The `send_tx` handler immediately calls `insert_try_to_send` with caller-supplied fields including `fee_paying_type`, `cancel_outpoints`, `cancel_txids`, `activate_txids`, and `activate_outpoints`, with no identity check: [2](#0-1) 

The configuration layer explicitly allows binding to `0.0.0.0`:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(BridgeError::EnvVarMalformed(...));
}
``` [3](#0-2) 

This contrasts sharply with the gRPC servers for operator and verifier, which enforce mTLS and the `OnlyAggregatorAndSelf` interceptor when `client_verification = true`: [4](#0-3) 

The gRPC interceptor correctly distinguishes internal methods (those whose URI path segment starts with `Internal`) from public ones, and rejects unauthenticated callers: [5](#0-4) 

No equivalent guard exists anywhere in the JSON-RPC path.

### Impact Explanation

The tx-sender queue drives every time-sensitive bridge transaction. `add_tx_to_queue` is called for `Challenge`, `DisproveTimeout`, `WatchtowerChallengeTimeout`, `AssertTimeout`, `Reimburse`, `Payout`, and others: [6](#0-5) 

An attacker who can reach the JSON-RPC port can:

1. **Queue-flood**: Submit thousands of syntactically valid but economically invalid transactions. The tx-sender processes the queue sequentially; a flooded queue delays legitimate entries.
2. **Activation manipulation**: Submit a new entry with `activate_txids` pointing to a txid that will never confirm, permanently blocking that entry from being sent — but more critically, the attacker can submit entries that consume the same fee-payer UTXOs the tx-sender would use for CPFP, exhausting the fee wallet.
3. **Liveness denial during timelock windows**: Bridge safety depends on challenge, disprove, and assert-timeout transactions landing within fixed Bitcoin block windows (e.g., `OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK = 144` blocks). A sustained flood during a live challenge can cause the operator to miss the window, forfeiting collateral (`OPERATOR_CHALLENGE_AMOUNT = 130,000,000 sats` in the regtest config). [7](#0-6) 

### Likelihood Explanation

The `TX_SENDER_JSONRPC_BIND` environment variable defaults to `127.0.0.1` in the example run script, but the code explicitly validates and accepts `0.0.0.0`. In containerised deployments (Docker Compose, Kubernetes), operators commonly bind services to `0.0.0.0` for inter-container reachability. Any attacker who reaches the internal network — through a compromised sidecar, misconfigured network policy, or exposed port — can exploit this with a single `curl` command, as demonstrated in the project's own smoke-test script: [8](#0-7) 

No credentials, certificates, or prior knowledge of bridge state are required.

### Recommendation

Add authentication to the JSON-RPC server. Options in order of preference:

1. **Shared secret / bearer token**: Require a configurable `Authorization: Bearer <token>` header; reject requests without it. This is the minimal fix.
2. **Bind-only to loopback**: Remove `0.0.0.0` as a valid option; force `127.0.0.1` and require callers to be co-located or use an authenticated reverse proxy.
3. **mTLS**: Mirror the gRPC server's `OnlyAggregatorAndSelf` pattern using a TLS-capable HTTP server.

At minimum, add a middleware layer to `start_jsonrpc_server` that checks a shared secret before dispatching any method.

### Proof of Concept

With `TX_SENDER_JSONRPC_BIND=0.0.0.0` and `TX_SENDER_JSONRPC_PORT=3030`:

```bash
# Flood the queue with 10,000 dummy entries during a live challenge window
for i in $(seq 1 10000); do
  curl -s -X POST http://<tx-sender-host>:3030 \
    -H 'content-type: application/json' \
    -d '{
      "jsonrpc":"2.0","id":1,"method":"send_tx",
      "params":[{
        "tx_metadata": null,
        "signed_tx_hex": "02000000000101000000000000000000000000000000000000000000000000000000000000000000000000ffffffff0100e1f50500000000160014deadbeefdeadbeefdeadbeefdeadbeefdeadbeef00000000",
        "fee_paying_type": "CPFP",
        "rbf_signing_info": null,
        "cancel_outpoints": [],
        "cancel_txids": [],
        "activate_txids": [],
        "activate_outpoints": []
      }]
    }' &
done
wait
```

No authentication is required. The tx-sender will attempt to process each entry, delaying legitimate challenge or disprove-timeout transactions. If the challenge window (e.g., 144 blocks ≈ 24 hours) expires before the legitimate transaction is processed, the operator's collateral is slashable. [9](#0-8)

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

**File:** crates/clementine-tx-sender/src/config.rs (L197-211)
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

**File:** core/src/tx_sender_queue.rs (L57-105)
```rust
        match tx_type {
            TransactionType::Kickoff
            | TransactionType::Dummy
            | TransactionType::ChallengeTimeout
            | TransactionType::DisproveTimeout
            | TransactionType::Reimburse
            | TransactionType::Round
            | TransactionType::OperatorChallengeNack(_)
            | TransactionType::UnspentKickoff(_)
            | TransactionType::MoveToVault
            | TransactionType::BurnUnusedKickoffConnectors
            | TransactionType::KickoffNotFinalized
            | TransactionType::MiniAssert(_)
            | TransactionType::LatestBlockhashTimeout
            | TransactionType::LatestBlockhash
            | TransactionType::EmergencyStop
            | TransactionType::OptimisticPayout
            | TransactionType::ReadyToReimburse
            | TransactionType::ReplacementDeposit
            | TransactionType::WatchtowerChallenge(_)
            | TransactionType::AssertTimeout(_) => {
                // no_dependency and cpfp
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::CPFP,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** scripts/docker/configs/regtest/.env.regtest (L33-44)
```text
OPERATOR_CHALLENGE_AMOUNT=130000000
COLLATERAL_FUNDING_AMOUNT=90000000
KICKOFF_BLOCKHASH_COMMIT_LENGTH=40
WATCHTOWER_CHALLENGE_BYTES=144
WINTERNITZ_LOG_D=4
WINTERNITZ_SECRET_KEY=2222222222222222222222222222222222222222222222222222222222222222
USER_TAKES_AFTER=200
OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK=144
OPERATOR_CHALLENGE_NACK_TIMELOCK=432
DISPROVE_TIMEOUT_TIMELOCK=720
ASSERT_TIMEOUT_TIMELOCK=576
OPERATOR_REIMBURSE_TIMELOCK=12
```

**File:** crates/clementine-tx-sender/run.sh (L86-100)
```shellscript
    # We intentionally DO NOT use --fail because jsonrpsee may return HTTP 400 for JSON-RPC errors.
    if curl -sS \
      -H 'content-type: application/json' \
      --data '{"jsonrpc":"2.0","id":1,"method":"send_tx","params":[]}' \
      "${url}" >/dev/null 2>&1; then
      return 0
    fi

    if (( "$(date +%s)" - start_ts > deadline )); then
      echo "Timed out waiting for txsender JSON-RPC at ${url}" >&2
      return 1
    fi
    sleep 0.2
  done
}
```
