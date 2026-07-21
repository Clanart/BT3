### Title
Unauthenticated `initialize()` on Remote L1Provider Server Allows Injection of Fake L1 Handler Transactions into Block Proposals - (File: crates/apollo_l1_provider/src/l1_provider.rs)

### Summary

`L1Provider` starts in `ProviderState::Uninitialized` and exposes its `initialize()` method through an unauthenticated `RemoteL1ProviderServer` bound to `0.0.0.0`. Any network peer that can reach the port before the legitimate `L1Scraper` can call `initialize()` with an arbitrary `start_height` and a crafted set of fake `L1HandlerTransaction` events. Once injected, those fake transactions are served to the proposer's `ProposeTransactionProvider`, executed by the blockifier, and committed into the state diff and block commitment — producing a wrong L1-message state root.

### Finding Description

`L1Provider::new()` always sets `state = ProviderState::Uninitialized`. [1](#0-0) 

`initialize()` is the only method that transitions the provider out of this state. It accepts an arbitrary `start_height` and a `Vec<Event>` that are added directly to the `TransactionManager` with no source validation: [2](#0-1) 

The `L1ProviderRequest::Initialize` variant is handled by the component server with no authentication: [3](#0-2) 

In distributed deployments the `RemoteL1ProviderServer` is created and bound to `0.0.0.0`: [4](#0-3) 

The deployment configuration confirms `bind_ip: "0.0.0.0"`: [5](#0-4) 

There is no credential check, IP allowlist, or TLS mutual-auth on the remote server. Any peer that can reach the port can send `L1ProviderRequest::Initialize`.

After a successful attacker-controlled `initialize()` call the provider is in `Pending` state with:
- `start_height` set to the attacker's chosen value (causing all real L1 handler transactions below that height to be silently dropped as "historical")
- Fake `L1HandlerTransaction` events resident in the `TransactionManager`

When the batcher subsequently calls `start_block(height, Propose)` the provider transitions to `Propose` state. `ProposeTransactionProvider::get_l1_handler_txs` then calls `get_txs` and receives the fake transactions: [6](#0-5) 

Note `.unwrap_or_default()` — errors are silently swallowed and the fake transactions are returned as valid. The batcher ignores `start_block` errors too: [7](#0-6) 

The fake transactions are then executed by the blockifier, their effects enter the `CommitmentStateDiff`, and `BlockExecutionArtifacts::new` computes `calculate_block_commitments` over the corrupted diff: [8](#0-7) 

The resulting `PartialBlockHashComponents` and `ThinStateDiff` are stored and used to derive the final block hash: [9](#0-8) 

When the legitimate `L1Scraper` subsequently calls `initialize()`, the provider panics (the FIXME-marked path): [10](#0-9) 

This kills the L1Provider process. The scraper retries, the provider restarts in `Uninitialized`, and the attacker can repeat the injection for the next block.

### Impact Explanation

Fake `L1HandlerTransaction` objects are executed by the blockifier as legitimate L2 transactions. Their storage writes, events, and fee effects enter the `CommitmentStateDiff`. The state-diff commitment, transaction commitment, event commitment, and receipt commitment are all computed over this corrupted data. The resulting `PartialBlockHashComponents` and final block hash are wrong. This is a **Critical** impact: wrong L1 message, wrong state, wrong block commitment accepted through normal execution logic.

### Likelihood Explanation

In a distributed deployment the `RemoteL1ProviderServer` is bound to `0.0.0.0` with no authentication. Any host on the same network segment (or any host if the port is reachable from outside) can send `L1ProviderRequest::Initialize` before the scraper completes its startup sequence. The scraper's startup involves multiple async retries (`fetch_start_block`, `get_last_historic_l2_height`, `initialize`) giving the attacker a window of several seconds. The attack is repeatable because each panic-restart resets the provider to `Uninitialized`.

### Recommendation

1. **Disable `Initialize` on the remote server.** The `initialize()` call should only be accepted from the co-located `L1Scraper` via the local (in-process) channel. Add a variant-level guard in `handle_request` that rejects `L1ProviderRequest::Initialize` when received over the remote transport.

2. **Authenticate the remote server.** Add mutual TLS or a shared-secret token to the `RemoteComponentServer` so that only trusted internal peers can send any request.

3. **Replace the panic with a graceful error.** The FIXME at line 106–113 acknowledges the panic is wrong. Return a `FatalError` that triggers a controlled restart instead of killing the process, so the scraper's retry loop does not create an infinite crash-restart cycle exploitable by the attacker.

### Proof of Concept

```
# Attacker (any host reachable to the L1Provider remote port) at node startup:

1. Connect to L1Provider remote server at 0.0.0.0:<L1_PROVIDER_PORT>

2. Send L1ProviderRequest::Initialize {
       historic_l2_height: <current_batcher_height>,   // matches batcher's next block
       events: [
           Event::L1HandlerTransaction {
               l1_handler_tx: <crafted_fake_tx>,        // arbitrary calldata / target
               block_timestamp: 0,
               scrape_timestamp: 0,
           }
       ]
   }

3. L1Provider transitions: Uninitialized → Pending
   tx_manager now contains <crafted_fake_tx>

4. Batcher calls start_block(height, Propose) → L1Provider: Pending → Propose
   Batcher calls get_txs(n, height) → returns [<crafted_fake_tx>]

5. Blockifier executes <crafted_fake_tx>:
   - Arbitrary storage writes committed to state diff
   - Wrong state_diff_commitment, wrong block hash

6. Legitimate scraper calls initialize() → L1Provider panics → process restarts
   Attacker repeats from step 2 for the next block.
```

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L84-84)
```rust
            state: ProviderState::Uninitialized,
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L99-126)
```rust
    pub async fn initialize(
        &mut self,
        start_height: BlockNumber,
        events: Vec<Event>,
    ) -> L1ProviderResult<()> {
        info!("Initializing l1 provider");
        if !self.state.is_uninitialized() {
            // FIXME: This should be return FatalError or similar, which should trigger a planned
            // restart from the infra, since this CAN happen if the scraper recovered from a crash.
            // Right now this is effectively a KILL message when called in steady state.
            panic!(
                "Called initialize while not in Uninitialized state. Restart service. Provider \
                 state: {:?}",
                self.state
            );
        };

        // The provider now goes into Pending state.
        // The current_height is set to a very old height, that doesn't include any of the events
        // sent now, or to be scraped in the future. The provider will begin catching up when the
        // batcher calls commit_block with a height above the current height.
        self.start_height = Some(start_height);
        self.current_height = start_height;
        self.state = ProviderState::Pending;
        self.add_events(events)?;

        Ok(())
    }
```

**File:** crates/apollo_l1_provider/src/communication.rs (L44-46)
```rust
            L1ProviderRequest::Initialize { historic_l2_height, events } => {
                L1ProviderResponse::Initialize(self.initialize(historic_l2_height, events).await)
            }
```

**File:** crates/apollo_node/src/servers.rs (L575-582)
```rust
    let l1_provider_server = create_remote_server!(
        &config.components.l1_provider.execution_mode,
        || { clients.get_l1_provider_local_client() },
        config.components.l1_provider.remote_server_config,
        config.components.l1_provider.port,
        config.components.l1_provider.max_concurrency,
        L1_PROVIDER_INFRA_METRICS.get_remote_server_metrics()
    );
```

**File:** crates/apollo_deployments/resources/services/distributed/replacer_l1.json (L82-82)
```json
  "components.l1_provider.remote_server_config.bind_ip": "0.0.0.0",
```

**File:** crates/apollo_batcher/src/transaction_provider.rs (L93-108)
```rust
    async fn get_l1_handler_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .l1_provider_client
            .get_txs(n_txs, self.height)
            .await
            .inspect_err(|_err| {
                BATCHER_L1_PROVIDER_ERRORS.increment(1);
            })
            .unwrap_or_default()
            .into_iter()
            .map(InternalConsensusTransaction::L1Handler)
            .collect())
    }
```

**File:** crates/apollo_batcher/src/batcher.rs (L302-314)
```rust
        // Ignore errors. If start_block fails, then subsequent calls to l1 provider will fail on
        // out of session and l1 provider will restart and bootstrap again.
        let _ = self
            .l1_provider_client
            .start_block(SessionState::Propose, propose_block_input.block_info.block_number)
            .await
            .inspect_err(|err| {
                error!(
                    "L1 provider is not ready to start proposing block {}: {}. ",
                    propose_block_input.block_info.block_number, err
                );
                BATCHER_L1_PROVIDER_ERRORS.increment(1);
            });
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-166)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L520-525)
```rust
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
                Ok(FinalBlockCommitment { height, block_hash: Some(block_hash), global_root })
```
