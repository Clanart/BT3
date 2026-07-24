### Title
Unencrypted HTTP Accepted for Citrea RPC and Light-Client-Prover Connections — No Scheme Enforcement in `CitreaClient::new` - (File: core/src/citrea.rs)

### Summary

Clementine's `CitreaClient` accepts any URL scheme for both `citrea_rpc_url` and `citrea_light_client_prover_url`. The code parses the URL but never validates that the scheme is `https://`. All shipped example and Docker configurations default to `http://` for the Citrea full-node RPC endpoint. An attacker positioned on the network path between a Clementine node and its Citrea RPC endpoint can intercept or tamper with withdrawal UTXO lists, deposit move-txid lists, and light-client proof responses, causing the operator to pay withdrawals to attacker-controlled Bitcoin addresses or to process forged bridge state.

### Finding Description

In `core/src/citrea.rs`, `CitreaClient::new` constructs three HTTP clients from the caller-supplied URLs:

```rust
let citrea_rpc_url = Url::parse(&citrea_rpc_url)...;   // scheme never checked
let provider = ProviderBuilder::new()
    .wallet(EthereumWallet::from(key))
    .on_http(citrea_rpc_url.clone());                   // alloy EVM provider
let client = HttpClientBuilder::default()
    .request_timeout(...)
    .build(citrea_rpc_url)...;                          // jsonrpsee JSON-RPC client
let light_client_prover_client = HttpClientBuilder::default()
    .request_timeout(...)
    .build(light_client_prover_url)...;                 // light-client-prover client
``` [1](#0-0) 

No guard rejects `http://` URLs. The `BridgeConfig` struct stores both fields as plain `String` with no scheme validation. [2](#0-1) 

Every shipped configuration file and the canonical `.env.example` sets `citrea_rpc_url` to a plaintext `http://` address: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The `scripts/run.sh` defaults also use `http://` for both Citrea URLs: [7](#0-6) 

Contrast this with the inter-node gRPC layer, which always enforces mTLS for TCP connections and rejects unauthenticated peers: [8](#0-7) 

The Citrea RPC layer has no equivalent enforcement.

### Impact Explanation

The `collect_withdrawal_utxos` and `collect_deposit_move_txids` methods drive the operator's core payout and deposit-finalization logic. Both are called over the unencrypted `client` / `provider` connections. A network-level attacker who can intercept the HTTP stream can:

1. **Withdrawal path** — return spoofed `WithdrawalUtxo` entries pointing to attacker-controlled Bitcoin addresses. The operator will construct and broadcast a payout transaction to those addresses, permanently losing the operator's bridged BTC.
2. **Deposit path** — suppress or forge `DepositMoved` events, causing the operator to skip legitimate deposits or double-process fabricated ones, corrupting bridge state.
3. **Light-client proof path** — return a structurally valid but semantically incorrect `LightClientProof` blob. If the ZK verifier accepts it (e.g., a replayed proof from a different block), the bridge circuit will bind the wrong chain state to a withdrawal, enabling theft of bridged BTC.

### Likelihood Explanation

In Docker-based deployments the Citrea node is a named container on the same Docker bridge network (`citrea_full_node`, `citrea_sequencer_regtest`). Any container on that bridge — including a compromised SDK, a misconfigured sidecar, or a container escape — can perform ARP spoofing or DNS poisoning against the unencrypted HTTP stream. In non-containerised deployments where the Citrea node is on a separate host (e.g., a managed RPC provider), the attack surface widens to any router or ISP on the path. The likelihood is **medium**: the attacker needs a foothold on the same Docker network or the network path, which is a realistic threat model for a production bridge.

### Recommendation

1. **Enforce HTTPS at startup**: In `CitreaClient::new`, validate that both `citrea_rpc_url` and `light_client_prover_url` use the `https` scheme and return a `BridgeError::ConfigError` if they do not.
2. **Update all shipped configurations** to use `https://` for both Citrea RPC fields.
3. **Pin the TLS certificate or CA** for the Citrea RPC endpoint so that a compromised CA cannot issue a fraudulent certificate.
4. **Add response integrity checks** for withdrawal UTXOs: cross-verify the returned UTXOs against a second source (e.g., a local Citrea full node or a signed Merkle proof from the contract) before constructing payout transactions.

### Proof of Concept

```
# Attacker on the same Docker bridge network as the Clementine operator container:

# 1. ARP-spoof the citrea_full_node container
arpspoof -i eth0 -t <clementine_operator_ip> <citrea_full_node_ip>

# 2. Run a transparent HTTP proxy that intercepts JSON-RPC responses
mitmproxy --mode transparent --listen-port 80 \
  --script inject_fake_withdrawal_utxos.py

# inject_fake_withdrawal_utxos.py replaces the `result` field of
# eth_getLogs / eth_call responses for the bridge contract's
# WithdrawalUtxo events with attacker-controlled Bitcoin outpoints.

# 3. The operator's next automation cycle calls collect_withdrawal_utxos(),
#    receives the spoofed UTXOs, constructs a payout tx to the attacker's
#    Bitcoin address, and broadcasts it — permanently losing operator BTC.
```

The root cause is in `core/src/citrea.rs` at the `CitreaClient::new` constructor, which builds all three HTTP clients without scheme validation. [9](#0-8)

### Citations

**File:** core/src/citrea.rs (L363-418)
```rust
    async fn new(
        citrea_rpc_url: String,
        light_client_prover_url: String,
        chain_id: u32,
        secret_key: Option<PrivateKeySigner>,
        timeout: Option<Duration>,
    ) -> Result<Self, BridgeError> {
        let citrea_rpc_url = Url::parse(&citrea_rpc_url).wrap_err("Can't parse Citrea RPC URL")?;
        let light_client_prover_url =
            Url::parse(&light_client_prover_url).wrap_err("Can't parse Citrea LCP RPC URL")?;
        let secret_key = secret_key.unwrap_or(PrivateKeySigner::random());

        let key = secret_key.with_chain_id(Some(chain_id.into()));

        #[cfg(test)]
        let wallet_address = key.address();

        tracing::info!("Wallet address: {}", key.address());

        let provider = ProviderBuilder::new()
            .wallet(EthereumWallet::from(key))
            .on_http(citrea_rpc_url.clone());

        tracing::info!("Provider created");

        let contract = BRIDGE_CONTRACT::new(
            BRIDGE_CONTRACT_ADDRESS
                .parse()
                .expect("Correct contract address"),
            provider,
        );

        tracing::info!("Contract created");

        let client = HttpClientBuilder::default()
            .request_timeout(timeout.unwrap_or(Duration::from_secs(60)))
            .build(citrea_rpc_url)
            .wrap_err("Failed to create Citrea RPC client")?;

        tracing::info!("Citrea RPC client created");

        let light_client_prover_client = HttpClientBuilder::default()
            .request_timeout(timeout.unwrap_or(Duration::from_secs(60)))
            .build(light_client_prover_url)
            .wrap_err("Failed to create Citrea LCP RPC client")?;

        tracing::info!("Citrea LCP RPC client created");

        Ok(CitreaClient {
            client,
            light_client_prover_client,
            #[cfg(test)]
            wallet_address,
            contract,
        })
    }
```

**File:** core/src/config/mod.rs (L82-84)
```rust
    pub citrea_rpc_url: String,
    /// Citrea light client prover RPC URL.
    pub citrea_light_client_prover_url: String,
```

**File:** core/src/test/data/bridge_config.toml (L44-46)
```text
# Citrea RPC URL.
citrea_rpc_url = "http://127.0.0.1:12345"
citrea_light_client_prover_url = "http://127.0.0.1:12346"
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L61-62)
```text
citrea_rpc_url = "http://citrea_full_node:12346"
citrea_light_client_prover_url = "https://light-client-prover.testnet.citrea.xyz/"
```

**File:** .env.example (L31-31)
```text
CITREA_LIGHT_CLIENT_PROVER_URL=http://127.0.0.1:12346
```

**File:** scripts/docker/configs/regtest/.env.regtest (L20-21)
```text
CITREA_RPC_URL=http://citrea_sequencer_regtest:12345
CITREA_LIGHT_CLIENT_PROVER_URL=http://citrea_light_client_prover_regtest:12349
```

**File:** scripts/run.sh (L32-33)
```shellscript
export CITREA_RPC_URL=${CITREA_RPC_URL:="http://127.0.0.1:12345"}
export CITREA_LIGHT_CLIENT_PROVER_URL=${CITREA_LIGHT_CLIENT_PROVER_URL:="http://127.0.0.1:12346"}
```

**File:** core/src/servers.rs (L106-112)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };
```
