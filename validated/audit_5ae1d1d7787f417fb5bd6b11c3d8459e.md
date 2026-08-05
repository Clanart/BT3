[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/repair/repair_service.rs (L1-56)
```rust
//! The `repair_service` module implements the tools necessary to generate a thread which
//! regularly finds missing shreds in the ledger and sends repair requests for those shreds
use {
    super::standard_repair_handler::StandardRepairHandler,
    crate::{
        cluster_slots_service::cluster_slots::ClusterSlots,
        repair::{
            ancestor_hashes_service::{
                AncestorHashesChannels, AncestorHashesReplayUpdateReceiver, AncestorHashesService,
            },
            duplicate_repair_status::AncestorDuplicateSlotToRepair,
            outstanding_requests::OutstandingRequests,
            repair_weight::RepairWeight,
            serve_repair::{
                REPAIR_PEERS_CACHE_CAPACITY, RepairPeers, RepairProtocol, RepairRequestHeader,
                ServeRepair, ShredRepairType,
            },
        },
    },
    agave_votor_messages::{VerifiedVoterSlotsReceiver, migration::MigrationStatus},
    ahash::AHashMap,
    bytes::Bytes,
    crossbeam_channel::{Receiver as CrossbeamReceiver, Sender as CrossbeamSender},
    lazy_lru::LruCache,
    rand::prelude::IndexedRandom as _,
    solana_clock::Slot,
    solana_epoch_schedule::EpochSchedule,
    solana_gossip::cluster_info::ClusterInfo,
    solana_hash::Hash,
    solana_keypair::Signer,
    solana_ledger::{
        blockstore::Blockstore,
        blockstore_meta::{BlockLocation, SlotMetaRepair},
        shred,
    },
    solana_measure::measure::Measure,
    solana_net_utils::{PinnedXdpSender, Protocol},
    solana_pubkey::Pubkey,
    solana_runtime::{
        bank::Bank,
        bank_forks::{BankForks, SharableBanks},
    },
    solana_streamer::sendmmsg::{SendPktsError, batch_send},
    solana_time_utils::timestamp,
    std::{
        collections::{HashMap, HashSet, hash_map::Entry},
        iter::Iterator,
        net::{SocketAddr, UdpSocket},
        sync::{
            Arc, RwLock,
            atomic::{AtomicBool, Ordering},
        },
        thread::{self, Builder, JoinHandle, sleep},
        time::{Duration, Instant},
    },
};
```

**File:** core/src/repair/repair_service.rs (L86-99)
```rust
/// Per-slot repair timing tracked at FEC granularity.
///
/// This intentionally avoids per-shred timestamps. `first_observed_at_ms`
/// is indexed by FEC set ordinal and stores when repair first observed, or
/// inferred from a later FEC, that the FEC set had started arriving. Observation
/// is based on `SlotMeta::received`, which is the highest received data shred
/// index plus one.
#[derive(Debug)]
struct SlotRepairFecTimes {
    /// Timestamp each FEC set in this slot was first observed, keyed by its
    /// ordinal. Observed here means a valid shred in this FEC set or a later
    /// FEC set was received.
    first_observed_at_ms: Vec<u64>,
}
```
