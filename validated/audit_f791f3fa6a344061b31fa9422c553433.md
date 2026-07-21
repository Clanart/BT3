### Title
Unauthenticated `AddGasPrice`/`Initialize` on `RemoteComponentServer<L1GasPriceRequest, L1GasPriceResponse>` Allows Any Network Peer to Inject Arbitrary L1 Gas Prices into Block Headers - (File: `crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs`, `crates/apollo_infra/src/component_server/remote_component_server.rs`)

---

### Summary

In distributed and hybrid deployments, the `L1GasPriceProvider` component is exposed over a plain HTTP/2 `RemoteComponentServer` bound to `0.0.0.0:<port>` with no authentication, no TLS, and no caller-identity check. The `L1GasPriceRequest` enum includes both `AddGasPrice` and `Initialize` variants, both of which mutate the provider's internal ring buffer. Any network peer that can reach the port can reset the ring buffer via `Initialize` and then inject arbitrary `base_fee_per_gas` / `blob_fee` values via `AddGasPrice`. The consensus orchestrator reads these prices and embeds them verbatim into every block header, directly corrupting the L1 gas price fields used for fee calculation across all transactions in the affected blocks.

---

### Finding Description

**Root cause — no access control on the write path of `L1GasPriceProvider`:**

`L1GasPriceProvider::add_price_info` accepts a `GasPriceData` struct containing `block_number`, `timestamp`, `base_fee_per_gas`, and `blob_fee`. The only validation performed is a consecutive-block-number check: [1](#0-0) 

There is no check on who the caller is. The `initialize()` method is equally unguarded: [2](#0-1) 

**Exposure path — `RemoteComponentServer` with no authentication:**

The `ComponentRequestHandler` implementation in `communication.rs` routes both `L1GasPriceRequest::AddGasPrice` and `L1GasPriceRequest::Initialize` directly to the provider with no caller check: [3](#0-2) 

The `RemoteComponentServer` that wraps this handler is a plain HTTP/2 TCP server. Its `start()` method binds to `SocketAddr::new(self.config.bind_ip, self.port)` and accepts every incoming TCP connection. The `remote_component_server_handler` function performs only deserialization — there is no TLS, no token check, no IP allowlist, and no per-variant authorization: [4](#0-3) 

The `RemoteServerConfig` struct contains no authentication fields whatsoever: [5](#0-4) 

**Deployment configuration confirms the server is bound to `0.0.0.0` in production:**

In both distributed and hybrid deployments, the `l1_gas_price_provider` is configured with `execution_mode: LocalExecutionWithRemoteEnabled` and `bind_ip: "0.0.0.0"`: [6](#0-5) 

**Attack sequence:**

1. Attacker sends `L1GasPriceRequest::Initialize` to the provider's HTTP/2 port. This resets `price_samples_by_block` to an empty `RingBuffer` (capacity = `storage_limit`, default 3000).
2. Because the ring buffer is now empty, the consecutive-block-number guard (`if let Some(data) = samples.back()`) is skipped on the first insertion.
3. Attacker sends 3000 `L1GasPriceRequest::AddGasPrice` messages with `block_number` 0..2999, `timestamp` = current time, and `base_fee_per_gas` / `blob_fee` set to any desired value (e.g., `u128::MAX` to maximize fees, or `1` to minimize them).
4. The ring buffer is now full of attacker-controlled price samples. The legitimate scraper's next `add_price_info` call will fail with `UnexpectedBlockNumberError` (expected block 3000, found the real current L1 block number), but the scraper only logs this and continues — it does not evict the fake data: [7](#0-6) 

5. The consensus orchestrator calls `get_l1_prices_in_fri_and_wei_and_conversion_rate`, which calls `l1_gas_price_provider_client.get_price_info(timestamp)`. This returns the mean of the attacker-controlled samples: [8](#0-7) 

6. The returned `PriceInfo` is embedded into every block header produced during the attack window, corrupting `l1_gas_price` and `l1_data_gas_price` for all transactions in those blocks.

---

### Impact Explanation

This matches the Critical impact scope: **"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

- Setting `base_fee_per_gas` and `blob_fee` to `u128::MAX` causes every transaction in the affected blocks to be charged at the maximum possible L1 gas price, draining user accounts.
- Setting them to `1` causes the sequencer to accept transactions at near-zero L1 cost, allowing economic exploitation of the fee model.
- The corrupted prices are written into the canonical block header and propagated to storage, affecting fee estimation, RPC responses, and any downstream proof inputs that consume block header fields.

---

### Likelihood Explanation

In any distributed or hybrid deployment (the production topology), the `L1GasPriceProvider` remote server is bound to `0.0.0.0` on a configured TCP port. No network-level authentication is enforced by the code itself. An attacker with access to the internal network segment (e.g., a compromised co-tenant pod in Kubernetes, a misconfigured network policy, or any node on the same VPC) can reach the port and execute the attack without any credentials. The `L1GasPriceRequest` enum is fully serializable and its wire format is straightforward `bincode`/`serde` encoding, making it trivial to craft valid requests.

---

### Recommendation

1. **Restrict write variants at the server layer.** The `RemoteComponentServer` should enforce that only the designated `L1GasPriceScraper` service can call `AddGasPrice` and `Initialize`. At minimum, add mTLS to the `RemoteComponentServer` so that only the scraper's certificate is accepted.
2. **Split read and write interfaces.** Expose `GetGasPrice` and `GetEthToFriRate` on a public-facing port; expose `AddGasPrice` and `Initialize` only on a localhost or loopback interface, or via a separate authenticated channel.
3. **Validate price bounds inside `add_price_info`.** Reject samples whose `base_fee_per_gas` or `blob_fee` deviate by more than a configurable factor from the previous sample, providing a defense-in-depth layer even if the transport is compromised.
4. **Add caller-identity fields to `RemoteServerConfig`** (e.g., allowed IP ranges, bearer token, or mTLS CA) and enforce them in `remote_component_server_handler` before dispatching any mutating request variant.

---

### Proof of Concept

```rust
// Pseudocode — attacker binary targeting the RemoteL1GasPriceServer port

use apollo_l1_gas_price_types::{GasPriceData, L1GasPriceRequest, PriceInfo};
use starknet_api::block::{BlockTimestamp, GasPrice};

async fn exploit(provider_url: &str) {
    let client = RemoteComponentClient::<L1GasPriceRequest, L1GasPriceResponse>::new(
        provider_url.parse().unwrap(),
    );

    // Step 1: Reset the ring buffer — no auth required.
    client.send(L1GasPriceRequest::Initialize).await.unwrap();

    // Step 2: Fill the ring buffer with MAX gas prices.
    for block_number in 0u64..3000 {
        let data = GasPriceData {
            block_number,
            timestamp: BlockTimestamp(current_unix_timestamp()),
            price_info: PriceInfo {
                base_fee_per_gas: GasPrice(u128::MAX),
                blob_fee: GasPrice(u128::MAX),
            },
        };
        client.send(L1GasPriceRequest::AddGasPrice(data)).await.unwrap();
    }

    // Result: every block produced from this point forward embeds
    // base_fee_per_gas = u128::MAX and blob_fee = u128::MAX,
    // causing all transactions to be charged at maximum L1 gas cost.
}
```

The legitimate scraper's subsequent `add_price_info(block_number = real_current_block)` calls will fail with `UnexpectedBlockNumberError` (expected 3000, found e.g. 21_000_000), be silently swallowed by the scraper's error handler, and the fake ring buffer will remain in place until the scraper is manually restarted and `Initialize` is called again — at which point the attacker can repeat the attack.

### Citations

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L78-82)
```rust
    pub fn initialize(&mut self) -> L1GasPriceProviderResult<()> {
        info!("Initializing L1GasPriceProvider with config: {:?}", self.config);
        self.price_samples_by_block = Some(RingBuffer::new(self.config.storage_limit));
        Ok(())
    }
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L84-103)
```rust
    pub fn add_price_info(&mut self, new_data: GasPriceData) -> L1GasPriceProviderResult<()> {
        // In case the provider has been restarted while the scraper is still running,
        // a NotInitializedError will be returned to the scraper. We expect the scraper to exit with
        // an error, and that infrastructure will restart it, leading to initialization.
        let Some(samples) = &mut self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        if let Some(data) = samples.back() {
            if new_data.block_number != data.block_number + 1 {
                return Err(L1GasPriceProviderError::UnexpectedBlockNumberError {
                    expected: data.block_number + 1,
                    found: new_data.block_number,
                });
            }
        }
        trace!("Received price sample for L1 block: {:?}", new_data);
        info_every_n_ms!(1_000, "Received price sample for L1 block: {:?}", new_data);
        samples.push(new_data);
        Ok(())
    }
```

**File:** crates/apollo_l1_gas_price/src/communication.rs (L20-37)
```rust
#[async_trait]
impl ComponentRequestHandler<L1GasPriceRequest, L1GasPriceResponse> for L1GasPriceProvider {
    #[instrument(skip(self))]
    async fn handle_request(&mut self, request: L1GasPriceRequest) -> L1GasPriceResponse {
        match request {
            L1GasPriceRequest::Initialize => L1GasPriceResponse::Initialize(self.initialize()),
            L1GasPriceRequest::GetGasPrice(timestamp) => {
                L1GasPriceResponse::GetGasPrice(self.get_price_info(timestamp))
            }
            L1GasPriceRequest::AddGasPrice(data) => {
                L1GasPriceResponse::AddGasPrice(self.add_price_info(data))
            }
            L1GasPriceRequest::GetEthToFriRate(timestamp) => {
                L1GasPriceResponse::GetEthToFriRate(self.eth_to_fri_rate(timestamp).await)
            }
        }
    }
}
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L70-77)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct RemoteServerConfig {
    pub max_streams_per_connection: u32,
    pub bind_ip: IpAddr,
    pub set_tcp_nodelay: bool,
    pub keepalive_interval_ms: u64,
    pub keepalive_timeout_ms: u64,
}
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L159-230)
```rust
    #[instrument(skip_all, fields(request_id = %request_id, remote_addr = %client_peer))]
    async fn remote_component_server_handler(
        http_request: HyperRequest<Incoming>,
        request_id: RequestId,
        client_peer: SocketAddr,
        local_client: LocalComponentClient<Request, Response>,
        metrics: &'static RemoteServerMetrics,
    ) -> Result<HyperResponse<Full<Bytes>>, hyper::Error> {
        trace!("Received HTTP request: {http_request:?}");
        let body_bytes = http_request.into_body().collect().await?.to_bytes();
        trace!("Extracted {} bytes from HTTP request body", body_bytes.len());

        metrics.increment_total_received();

        let http_response = match SerdeWrapper::<Request>::wrapper_deserialize(&body_bytes)
            .map_err(|err| ClientError::ResponseDeserializationFailure(err.to_string()))
        {
            Ok(request) => {
                trace!(
                    remote_addr = %client_peer,
                    request_id = %request_id,
                    request_type = request.request_label(),
                    "remote component request",
                );
                trace!("Successfully deserialized request: {request:?}");
                metrics.increment_valid_received();

                // Wrap the send operation in a tokio::spawn as it is NOT a cancel-safe operation.
                // Even if the current task is cancelled, the inner task will continue to run.
                // Note: this creates a new request ID for the local client.
                let response = tokio::spawn(async move { local_client.send(request).await })
                    .await
                    .expect("Should be able to extract value from the task");

                metrics.increment_processed();

                match response {
                    Ok(response) => {
                        trace!("Local client processed request successfully: {response:?}");
                        HyperResponse::builder()
                            .status(StatusCode::OK)
                            .header(CONTENT_TYPE, APPLICATION_OCTET_STREAM)
                            .body(Full::new(Bytes::from(
                                SerdeWrapper::new(response)
                                    .wrapper_serialize()
                                    .expect("Response serialization should succeed"),
                            )))
                    }
                    Err(error) => {
                        panic!(
                            "Remote server failed sending with its local client. Error: {error:?}"
                        );
                    }
                }
            }
            Err(error) => {
                error!("Failed to deserialize request: {error:?}");
                let server_error = ServerError::RequestDeserializationFailure(error.to_string());
                HyperResponse::builder().status(StatusCode::BAD_REQUEST).body(Full::new(
                    Bytes::from(
                        SerdeWrapper::new(server_error)
                            .wrapper_serialize()
                            .expect("Server error serialization should succeed"),
                    ),
                ))
            }
        }
        .expect("Response building should succeed");
        trace!("Built HTTP response: {http_response:?}");

        Ok(http_response)
    }
```

**File:** crates/apollo_deployments/resources/services/distributed/replacer_l1.json (L61-70)
```json
  "components.l1_gas_price_provider.max_concurrency": 128,
  "components.l1_gas_price_provider.port": "$$$_COMPONENTS-L1_GAS_PRICE_PROVIDER-PORT_$$$",
  "components.l1_gas_price_provider.remote_client_config.#is_none": true,
  "components.l1_gas_price_provider.remote_server_config.#is_none": false,
  "components.l1_gas_price_provider.remote_server_config.bind_ip": "0.0.0.0",
  "components.l1_gas_price_provider.remote_server_config.keepalive_interval_ms": 30000,
  "components.l1_gas_price_provider.remote_server_config.keepalive_timeout_ms": 10000,
  "components.l1_gas_price_provider.remote_server_config.max_streams_per_connection": 8,
  "components.l1_gas_price_provider.remote_server_config.set_tcp_nodelay": true,
  "components.l1_gas_price_provider.url": "$$$_COMPONENTS-L1_GAS_PRICE_PROVIDER-URL_$$$",
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_scraper.rs (L99-115)
```rust
        loop {
            // If we get an Ok() we just keep going with the loop.
            if let Err(e) = self.update_prices(&mut block_number).await {
                error!("Error while scraping gas prices: {e:?}");

                match e {
                    L1GasPriceScraperError::BaseLayerError(_) => {
                        L1_GAS_PRICE_SCRAPER_BASELAYER_ERROR_COUNT.increment(1);
                    }
                    // If we had a reorg, we must stop and restart the scraper.
                    L1GasPriceScraperError::L1ReorgDetected { .. } => return Err(e),
                    _ => {}
                }
            }
            L1_GAS_PRICE_SCRAPER_LATEST_SCRAPED_BLOCK.set_lossy(block_number);
            tokio::time::sleep(self.config.polling_interval).await;
        }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L147-174)
```rust
    let (eth_to_fri_rate, price_info) = tokio::join!(
        l1_gas_price_provider_client.get_eth_to_fri_rate(timestamp),
        l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
    );
    if price_info.is_err() {
        warn!("Failed to get l1 gas price from provider: {:?}", price_info);
        CONSENSUS_L1_GAS_PRICE_PROVIDER_ERROR.increment(1);
    }
    if eth_to_fri_rate.is_err() {
        warn!("Failed to get eth to fri rate from oracle: {:?}", eth_to_fri_rate);
    }
    if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
        // Both L1 prices and rate are Ok, so we can use them.
        info!(
            "raw eth_to_fri_rate (from oracle): {eth_to_fri_rate}, raw l1 gas price wei (from \
             provider): {price_info:?}"
        );
        apply_fee_transformations(&mut price_info, gas_price_params);
        let prices_in_wei = L1PricesInWei {
            l1_gas_price: price_info.base_fee_per_gas,
            l1_data_gas_price: price_info.blob_fee,
        };
        // Apply the eth/strk rate to get prices in fri.
        let l1_gas_prices_fri_result =
            L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
        // If conversion fails, leave return_value=None to try backup methods.
        if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
            return (prices_in_fri, prices_in_wei, eth_to_fri_rate);
```
