### Title
P2P Sync Client Never Registers `Protocol::Event` Sender Despite Server-Side Handler Existing — (`crates/apollo_state_sync/src/runner/mod.rs`)

### Summary

The `P2pSyncServer` registers and actively handles `Protocol::Event` inbound queries, but `P2pSyncClient` never registers a corresponding outbound event sender. Nodes running in P2P-sync mode therefore never acquire event data. Any `starknet_getEvents` RPC call on such a node returns an authoritative-looking empty result, and the node cannot independently verify the event commitment embedded in block hashes it has accepted.

### Finding Description

**Server side** — `StateSyncRunner::new_p2p_state_sync_server()` registers five protocols, including `Protocol::Event`:

```rust
let event_server_receiver =
    network_manager.register_sqmr_protocol_server(Protocol::Event.into(), BUFFER_SIZE);
```

`P2pSyncServerChannels` carries `event_receiver: EventReceiver` and `P2pSyncServer::run()` selects on it in its main loop, ready to serve `(Event, TransactionHash)` responses to any peer that asks.

**Client side** — `StateSyncRunner::new_p2p_state_sync_client()` registers only four protocols:

```rust
let header_client_sender   = network_manager.register_sqmr_protocol_client(Protocol::SignedBlockHeader.into(), BUFFER_SIZE);
let state_diff_client_sender = network_manager.register_sqmr_protocol_client(Protocol::StateDiff.into(), BUFFER_SIZE);
let transaction_client_sender = network_manager.register_sqmr_protocol_client(Protocol::Transaction.into(), BUFFER_SIZE);
let class_client_sender    = network_manager.register_sqmr_protocol_client(Protocol::Class.into(), BUFFER_SIZE);
```

`Protocol::Event` is never registered. `P2pSyncClientChannels` has no `event_sender` field, and `P2pSyncClientChannels::create_stream()` merges only four streams — header, state-diff, transaction, class — with no event stream. Events are therefore never requested from peers and never written to storage.

This is the direct Sequencer analog of the external report: the receiving side (`P2pSyncServer`) has a fully wired endpoint for a data type (`Event`), but the requesting side (`P2pSyncClient`) has no corresponding trigger to initiate that request — exactly as `mErcHost` had `liquidateExternal` while `mTokenGateway` lacked `liquidateOnHost`.

### Impact Explanation

A node configured for P2P sync will have empty event storage for every synced block. When a user or application calls `starknet_getEvents`, the RPC layer reads from that storage and returns an empty (or truncated) result set even for blocks that contain many events. The node presents itself as a complete full node, so callers have no indication the data is missing — this is an authoritative-looking wrong value.

Additionally, the Starknet block hash includes an event commitment (Poseidon hash over all events in the block). A P2P-synced node that never stores events cannot independently recompute or verify this commitment for any block it has accepted, weakening its ability to detect a dishonest peer that serves a block with a forged event commitment.

Impact category: **High — RPC execution returns an authoritative-looking wrong value** (`starknet_getEvents` on a P2P-synced node).

### Likelihood Explanation

P2P sync is a first-class production configuration option (toggled via `p2p_sync_client_config`). Any operator who deploys a node in P2P-sync mode — the mode intended for decentralised operation — is affected unconditionally. No special attacker action is required; the missing registration is a structural gap that fires on every block synced via P2P.

### Recommendation

In `StateSyncRunner::new_p2p_state_sync_client()`, register `Protocol::Event` as a client sender:

```rust
let event_client_sender =
    network_manager.register_sqmr_protocol_client(Protocol::Event.into(), BUFFER_SIZE);
```

Add `event_sender: EventSqmrSender` to `P2pSyncClientChannels`, implement an `EventStreamBuilder` analogous to `TransactionStreamFactory` / `ClassStreamBuilder`, and merge the resulting event stream inside `P2pSyncClientChannels::create_stream()` so that event data is fetched, validated, and written to storage for every synced block.

### Proof of Concept

1. Start a full sequencer node (node A) that produces blocks containing events (e.g., ERC-20 transfers).
2. Start a second node (node B) configured with `p2p_sync_client_config` set and `central_sync_client_config = None`.
3. Let node B sync several blocks from node A via P2P.
4. On node B, call `starknet_getEvents` for the synced block range.
5. Observe: the response is empty despite node A returning the correct events for the same range — the missing `Protocol::Event` client registration means node B's event storage was never populated.

**Relevant code locations:**

- Server registers `Protocol::Event`: [1](#0-0) 
- Client omits `Protocol::Event`: [2](#0-1) 
- `P2pSyncClientChannels` struct (no event field): [3](#0-2) 
- `create_stream` merges only four streams: [4](#0-3) 
- `P2pSyncServerChannels` carries `event_receiver`: [5](#0-4) 
- Server loop selects on `event_receiver`: [6](#0-5) 
- `Protocol::Event` defined in the shared enum: [7](#0-6)

### Citations

**File:** crates/apollo_state_sync/src/runner/mod.rs (L362-369)
```rust
        let header_client_sender = network_manager
            .register_sqmr_protocol_client(Protocol::SignedBlockHeader.into(), BUFFER_SIZE);
        let state_diff_client_sender =
            network_manager.register_sqmr_protocol_client(Protocol::StateDiff.into(), BUFFER_SIZE);
        let transaction_client_sender = network_manager
            .register_sqmr_protocol_client(Protocol::Transaction.into(), BUFFER_SIZE);
        let class_client_sender =
            network_manager.register_sqmr_protocol_client(Protocol::Class.into(), BUFFER_SIZE);
```

**File:** crates/apollo_state_sync/src/runner/mod.rs (L399-400)
```rust
        let event_server_receiver =
            network_manager.register_sqmr_protocol_server(Protocol::Event.into(), BUFFER_SIZE);
```

**File:** crates/apollo_p2p_sync/src/client/mod.rs (L74-79)
```rust
pub struct P2pSyncClientChannels {
    header_sender: HeaderSqmrSender,
    state_diff_sender: StateSqmrDiffSender,
    transaction_sender: TransactionSqmrSender,
    class_sender: ClassSqmrSender,
}
```

**File:** crates/apollo_p2p_sync/src/client/mod.rs (L132-133)
```rust
        header_stream.merge(state_diff_stream).merge(transaction_stream).merge(class_stream)
    }
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L83-89)
```rust
pub struct P2pSyncServerChannels {
    header_receiver: HeaderReceiver,
    state_diff_receiver: StateDiffReceiver,
    transaction_receiver: TransactionReceiver,
    class_receiver: ClassReceiver,
    event_receiver: EventReceiver,
}
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L151-156)
```rust
                maybe_server_query_manager = event_receiver.next() => {
                    let server_query_manager = maybe_server_query_manager.expect(
                        "Event queries sender was unexpectedly dropped."
                    );
                    register_query(self.storage_reader.clone(), server_query_manager, self.class_manager_client.clone(), "event");
                }
```

**File:** crates/apollo_p2p_sync/src/lib.rs (L10-16)
```rust
pub enum Protocol {
    SignedBlockHeader,
    StateDiff,
    Transaction,
    Class,
    Event,
}
```
