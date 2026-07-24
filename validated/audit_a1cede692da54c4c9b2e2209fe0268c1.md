The full call chain is confirmed. Let me verify the bind address configuration and whether there's any middleware or auth layer I may have missed.

### Title
Unauthenticated JSON-RPC `send_citrea_tx` Allows Any Network Caller to Queue Arbitrary DA Payloads and Drain Operator Bitcoin UTXOs — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The tx-sender JSON-RPC server is started with `ServerBuilder::default()` — plain HTTP, no TLS, no API key, no token, no IP allowlist beyond the bind address. Any caller who can reach the port can invoke `send_citrea_tx` and inject arbitrary `CitreaTxRequest` payloads (`BatchProof`, `BatchProofMethodId`, `SequencerCommitment`) into `tx_sender_citrea_raw_tx_queue`. The tx-sender loop then unconditionally picks up every queued row and broadcasts it on Bitcoin via the commit-reveal pattern, spending the operator's Bitcoin UTXOs on fees.

---

### Finding Description

`start_jsonrpc_server` builds the server with no authentication layer:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
    ...
``` [1](#0-0) 

The `send_citrea_tx` method is registered with no credential check of any kind:

```rust
module
    .register_async_method("send_citrea_tx", |params, client, _| async move {
        let req: InsertCitreaRawTxParams = params.one().map_err(jsonrpc_err)?;
        let insertion_id = client
            .send_citrea_tx(req.citrea_tx_request)
            .await
            .map_err(jsonrpc_err)?;
        Ok::<i64, ErrorObjectOwned>(insertion_id)
    })
``` [2](#0-1) 

This is in sharp contrast to the gRPC servers (operator, verifier, aggregator), which enforce mTLS + certificate-pinned interceptors: [3](#0-2) 

The bind address is configurable to `0.0.0.0`, making the port reachable from any network interface: [4](#0-3) 

`TxSenderClient::send_citrea_tx` directly inserts the attacker-supplied payload into `tx_sender_citrea_raw_tx_queue` with no origin validation: [5](#0-4) 

The DB insert path (`insert_citrea_raw_tx_single`) writes `transaction_kind`, `body`, and `body_hash` rows unconditionally: [6](#0-5) 

---

### Impact Explanation

The tx-sender loop processes every row in `tx_sender_citrea_raw_tx_queue` and broadcasts it on Bitcoin via the commit-reveal pattern, spending the operator's Bitcoin UTXOs on transaction fees. An attacker who can reach the JSON-RPC port can:

1. **Drain operator Bitcoin UTXOs**: By flooding the queue with large `BatchProof` payloads (up to the 50 MB request body limit per call), the tx-sender will attempt to broadcast each one, paying Bitcoin fees from the operator's wallet for every commit and reveal transaction.
2. **Inject unauthorized DA payloads**: Arbitrary `BatchProofMethodId` or `SequencerCommitment` data is broadcast on Bitcoin as if originating from the legitimate tx-sender, corrupting the DA layer and potentially disrupting Citrea bridge liveness.
3. **Liveness disruption**: Flooding the queue delays legitimate bridge payloads from being processed, breaking the bridge's DA submission liveness. [7](#0-6) 

---

### Likelihood Explanation

- The bind address can be `0.0.0.0`, making the port reachable from any host on the network.
- Even with `127.0.0.1`, any co-located process (container sidecar, compromised dependency, shared host) can reach it.
- No credentials, tokens, or certificates are required — a single `curl` or HTTP POST suffices.
- The `JsonRpcTxSenderClient` in the public `tx-sender-jsonrpc-client` crate documents the exact wire format, lowering the bar for exploitation. [8](#0-7) 

---

### Recommendation

Add an authentication layer to `start_jsonrpc_server`. Options in order of preference:

1. **Shared secret / bearer token**: Require a configurable `Authorization: Bearer <token>` header on every request, checked via a Tower middleware layer before dispatching to the `RpcModule`.
2. **Bind-only to loopback and enforce Unix socket**: Restrict the bind to a Unix domain socket path, eliminating TCP network exposure entirely.
3. **mTLS via `hyper`/`rustls` wrapping**: Wrap the `jsonrpsee` server in a TLS acceptor that requires a client certificate from the same CA used for gRPC actors.

At minimum, the `0.0.0.0` bind option should be removed or gated behind an explicit opt-in with a documented security warning, and a shared-secret check must be added before any method dispatch.

---

### Proof of Concept

```rust
// No credentials needed. Point at a running TxSenderJsonRpcServer.
let client = JsonRpcTxSenderClient::new("http://tx-sender-host:PORT").unwrap();

// Inject an attacker-crafted BatchProofMethodId payload.
let insertion_id = client
    .send_citrea_tx(CitreaTxRequest::BatchProofMethodId(vec![0xde, 0xad, 0xbe, 0xef]))
    .await
    .expect("no auth error — row inserted");

// The tx-sender loop will now broadcast this on Bitcoin, spending operator UTXOs.
println!("Injected row insertion_id={insertion_id}");
```

This matches the pattern already demonstrated by the existing (unauthenticated) test in `server.rs`: [9](#0-8)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-50)
```rust
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L94-105)
```rust
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
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L139-142)
```rust
        let (addr, handle) = spawn_txsender_loop_with_free_localhost_jsonrpc_port(tx_sender_cfg);
        let url = format!("http://{addr}");
        let client =
            JsonRpcTxSenderClient::new(&url).map_err(|e| BridgeError::Eyre(eyre::eyre!(e)))?;
```

**File:** core/src/rpc/interceptors.rs (L36-76)
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
```

**File:** crates/clementine-tx-sender/src/config.rs (L201-209)
```rust
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
```

**File:** crates/clementine-tx-sender/src/client.rs (L182-259)
```rust
    pub async fn send_citrea_tx(&self, request: CitreaTxRequest) -> Result<i64, eyre::Report> {
        use crate::citrea::data_serialization::DataOnDa;
        use crate::citrea::MAX_CHUNK_SIZE;

        let mut dbtx = self.db.begin_transaction().await?;

        let insertion_id = match request {
            CitreaTxRequest::BatchProof { bytes, chunk_size } => {
                // Hash the original proof bytes so the same proof dedupes even if callers
                // retry it with a different chunk_size or as a non-chunked Complete body.
                let full_body_hash = crate::citrea::calculate_sha256(&bytes);
                let mut chunk_size = chunk_size.unwrap_or(MAX_CHUNK_SIZE);
                if chunk_size == 0 {
                    chunk_size = MAX_CHUNK_SIZE;
                }
                if chunk_size > MAX_CHUNK_SIZE {
                    chunk_size = MAX_CHUNK_SIZE;
                }
                let chunk_size = chunk_size as usize;

                if bytes.len() <= chunk_size {
                    let data = DataOnDa::Complete(bytes);
                    let blob = borsh::to_vec(&data).expect("zk::Proof serialize must not fail");
                    self.db
                        .insert_citrea_raw_tx_single_with_hash(
                            &mut dbtx,
                            TransactionKind::Complete,
                            &blob,
                            &full_body_hash,
                        )
                        .await?
                } else {
                    let chunks: Vec<Vec<u8>> = bytes
                        .chunks(chunk_size)
                        .map(|chunk| {
                            borsh::to_vec(&DataOnDa::Chunk(chunk.to_vec()))
                                .expect("zk::Proof serialize must not fail")
                        })
                        .collect();
                    self.db
                        .insert_citrea_raw_tx_chunks(&mut dbtx, &chunks, &full_body_hash)
                        .await?
                }
            }
            CitreaTxRequest::BatchProofMethodId(body) => {
                if body.len() as u32 > MAX_CHUNK_SIZE {
                    return Err(eyre!(
                        "Citrea BatchProofMethodId DA payload body too large; max {} bytes",
                        MAX_CHUNK_SIZE,
                    ));
                }
                self.db
                    .insert_citrea_raw_tx_single(
                        &mut dbtx,
                        TransactionKind::BatchProofMethodId,
                        &body,
                    )
                    .await?
            }
            CitreaTxRequest::SequencerCommitment(body) => {
                if body.len() as u32 > MAX_CHUNK_SIZE {
                    return Err(eyre!(
                        "Citrea SequencerCommitment DA payload body too large; max {} bytes",
                        MAX_CHUNK_SIZE,
                    ));
                }
                self.db
                    .insert_citrea_raw_tx_single(
                        &mut dbtx,
                        TransactionKind::SequencerCommitment,
                        &body,
                    )
                    .await?
            }
        };

        self.db.commit_transaction(dbtx).await?;
        Ok(insertion_id)
```

**File:** crates/clementine-tx-sender/src/db/citrea.rs (L61-72)
```rust
    pub async fn insert_citrea_raw_tx_single(
        &self,
        tx: TxSenderDbTx<'_>,
        transaction_kind: TransactionKind,
        body: &[u8],
    ) -> Result<i64, BridgeError> {
        let body_hash = calculate_sha256(body);
        let (insertion_id, _) = self
            .insert_citrea_raw_tx_with_hash_status(tx, transaction_kind, Some(body), &body_hash)
            .await?;
        Ok(insertion_id)
    }
```

**File:** crates/clementine-tx-sender/src/citrea/reveal_scripts.rs (L44-56)
```rust
    pub fn create_reveal_script(
        &self,
        transaction_kind: TransactionKind,
        body: &[u8],
    ) -> CitreaSigningData {
        create_reveal_script(
            self.xonly_public_key(),
            &self.da_signer,
            self.network,
            transaction_kind,
            body,
        )
    }
```

**File:** crates/tx-sender-jsonrpc-client/src/lib.rs (L67-77)
```rust
    #[cfg(feature = "citrea")]
    pub async fn send_citrea_tx(
        &self,
        citrea_tx_request: CitreaTxRequest,
    ) -> Result<i64, JsonRpcError> {
        let req = InsertCitreaRawTxParams { citrea_tx_request };

        self.inner
            .request::<i64, _>("send_citrea_tx", rpc_params![req])
            .await
    }
```
