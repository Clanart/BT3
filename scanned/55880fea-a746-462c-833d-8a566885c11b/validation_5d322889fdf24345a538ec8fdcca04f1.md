## Analog Found

### Title
Hardcoded 1-SOL Gossip Anti-Spam Threshold (`MIN_STAKE_FOR_GOSSIP`) Assumes Fixed Token Value, Allowing Cheap Sybil Flooding of Consensus-Relevant Gossip Messages - (File: `gossip/src/crds_filter.rs`)

### Summary
`gossip/src/crds_filter.rs` hardcodes `MIN_STAKE_FOR_GOSSIP` as exactly `1 * LAMPORTS_PER_SOL` [1](#0-0) . This constant is the sole economic gate deciding whether a node's `DuplicateShred`, `RestartHeaviestFork`, `RestartLastVotedForkSlots`, `EpochSlots`, and pull-response `Vote` CRDS values are propagated through gossip once the cluster has ≥500 staked identities [2](#0-1) . Exactly like the reported ante bug (a fixed native-token amount meant to represent "meaningful economic cost" that stops making sense once the token's market price changes), this filter assumes 1 SOL will always remain expensive enough to make Sybil identity creation costly. It never adapts to SOL's market price.

### Finding Description
`should_retain_crds_value()` uses `retain_if_staked()`, which only checks `stake >= MIN_STAKE_FOR_GOSSIP` (1 SOL in absolute lamports) [3](#0-2) . Unlike the SWQoS stream-throttling logic elsewhere in the codebase, which computes a *stake ratio relative to total network stake* and therefore self-adjusts as the market/total-stake distribution changes [4](#0-3) , this gossip filter uses a **fixed absolute quantity of the native token**. Any unprivileged actor can:
1. Generate an arbitrary number of validator identity/vote/stake keypairs (no special privilege needed — this is a normal, permissionless system/stake-program operation).
2. Self-delegate slightly more than 1 SOL to each identity.
3. Once each identity's self-stake clears `MIN_STAKE_FOR_GOSSIP`, `retain_if_staked()` returns `true` for that pubkey, so gossip will propagate that identity's `DuplicateShred`, `RestartHeaviestFork`, `RestartLastVotedForkSlots`, and `Vote` values cluster-wide instead of treating it as low-value spam.

There is no other mechanism in the constant itself that re-derives "sufficiently expensive" from current market price — it is a compile-time constant. If SOL's market price falls (directly analogous to the report's BERA-vs-ETH price mismatch), the USD cost of clearing this threshold for many identities collapses, and the anti-spam assumption baked into the constant silently breaks while the code continues to treat the identities as "staked" and worthy of full propagation.

### Impact Explanation
Once past the filter, these CRDS types feed expensive downstream paths:
- `DuplicateShred` values are reconstructed and, upon completion, trigger `blockstore.store_duplicate_slot()` and a signal to the duplicate-slot consensus channel [5](#0-4) .
- `RestartHeaviestFork`/`RestartLastVotedForkSlots`/`EpochSlots` are consensus/restart-related messages that unstaked nodes are explicitly barred from injecting because of bandwidth/processing cost [6](#0-5) .

A cheap-Sybil-stake attacker who cleared the fixed 1-SOL bar across many identities can flood the cluster's gossip layer with these values from many distinct "staked" pubkeys simultaneously, causing CRDS table churn/bloat, increased duplicate-shred processing load, and general gossip bandwidth/CPU exhaustion across all unprivileged nodes in the cluster — a non-RPC remote resource-exhaustion vector, not one requiring any trusted/privileged role.

### Likelihood Explanation
Likelihood scales directly and only with SOL's market price, exactly as in the original report. At today's SOL price this attack is expensive, but the code contains no mechanism to keep the "1 SOL" bar economically meaningful if the price drops substantially — the same class of assumption failure flagged in the ante report. Because acquiring "staked" status here only requires normal permissionless staking operations (no validator approval, no minimum uptime, no vote-credit history), the barrier is purely the USD cost of `1 SOL x N` identities, which is not fixed over time.

### Recommendation
Do not gate this anti-spam control on an absolute native-token quantity. Compute the threshold relative to total network stake (as already done in `streamer/src/nonblocking/swqos.rs`'s `min_stake_ratio` logic [7](#0-6) ), or make it a cluster-configurable/feature-gated parameter that can be adjusted without a client release when token economics shift, rather than a hardcoded `LAMPORTS_PER_SOL` constant.

### Proof of Concept
1. Observe `MIN_STAKE_FOR_GOSSIP` is fixed at `LAMPORTS_PER_SOL` regardless of price [1](#0-0) .
2. Under `retain_if_staked()`, the only requirement to be treated as "staked" for full-CRDS-propagation purposes is `stake >= MIN_STAKE_FOR_GOSSIP` [3](#0-2) ; there is no relative-to-network-stake normalization here, unlike the SwQoS stream throttling logic elsewhere in the same codebase.
3. Create N validator identities, self-delegate `1 SOL + ε` lamports to each via the standard stake program (permissionless).
4. Each identity now satisfies `retain_if_staked()`, so gossip will forward their `DuplicateShred`/`RestartHeaviestFork`/`RestartLastVotedForkSlots`/`Vote` CRDS entries across the cluster instead of discarding them as low-value spam, at a USD cost that shrinks proportionally to any drop in SOL's market price.

### Citations

**File:** gossip/src/crds_filter.rs (L16-18)
```rust
/// Minimum stake that a node should have so that all its CRDS values are
/// propagated through gossip (below this only subset of CRDS is propagated).
pub(crate) const MIN_STAKE_FOR_GOSSIP: u64 = solana_native_token::LAMPORTS_PER_SOL;
```

**File:** gossip/src/crds_filter.rs (L27-68)
```rust
pub(crate) fn should_retain_crds_value(
    value: &CrdsValue,
    stakes: &HashMap<Pubkey, u64>,
    direction: GossipFilterDirection,
    is_full_alpenglow_epoch: bool,
) -> bool {
    let retain_if_staked = || {
        stakes.len() < MIN_NUM_STAKED_NODES || {
            let stake = stakes.get(&value.pubkey()).copied();
            stake.unwrap_or_default() >= MIN_STAKE_FOR_GOSSIP
        }
    };

    use GossipFilterDirection::*;
    match value.data() {
        // All nodes can send ContactInfo
        CrdsData::ContactInfo(_) => true,
        // Unstaked nodes can still serve snapshots.
        CrdsData::SnapshotHashes(_) => true,
        // Disabled once Alpenglow is active.
        CrdsData::DuplicateShred(_, _) => !is_full_alpenglow_epoch && retain_if_staked(),
        // Consensus related messages only allowed for staked nodes
        CrdsData::LowestSlot(0, _)
        | CrdsData::RestartHeaviestFork(_)
        | CrdsData::RestartLastVotedForkSlots(_) => retain_if_staked(),
        CrdsData::EpochSlots(_, _) if is_full_alpenglow_epoch => false,
        // Unstaked nodes can technically send EpochSlots, but we do not want them
        // eating gossip bandwidth.
        CrdsData::EpochSlots(_, _) => {
            match direction {
                // always store if we have received them
                // to avoid getting them again in PullResponses
                Ingress => true,
                // only forward if the origin is staked
                EgressPush | EgressPullResponse => retain_if_staked(),
            }
        }
        CrdsData::Vote(_, _) if is_full_alpenglow_epoch => false,
        CrdsData::Vote(_, _) => match direction {
            Ingress | EgressPush => true,
            EgressPullResponse => retain_if_staked(),
        },
```

**File:** streamer/src/nonblocking/swqos.rs (L314-329)
```rust
            |(pubkey, stake, total_stake)| {
                // The heuristic is that the stake should be large enough to have 1 stream pass through within one throttle
                // interval during which we allow max (MAX_STREAMS_PER_MS * STREAM_THROTTLING_INTERVAL_MS) streams.

                let peer_type = {
                    let max_streams_per_ms = self.staked_stream_load_ema.max_streams_per_ms();
                    let min_stake_ratio =
                        1_f64 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS) as f64;
                    let stake_ratio = stake as f64 / total_stake as f64;
                    if stake_ratio < min_stake_ratio {
                        // If it is a staked connection with ultra low stake ratio, treat it as unstaked.
                        ConnectionPeerType::Unstaked
                    } else {
                        ConnectionPeerType::Staked(stake)
                    }
                };
```

**File:** gossip/src/duplicate_shred_handler.rs (L108-157)
```rust
    fn handle_shred_data(&mut self, chunk: DuplicateShred) -> Result<(), Error> {
        if !self.should_consume_slot(chunk.slot) {
            return Ok(());
        }
        let slot = chunk.slot;
        let num_chunks = chunk.num_chunks();
        let chunk_index = chunk.chunk_index();
        if usize::from(num_chunks) > MAX_NUM_CHUNKS || chunk_index >= num_chunks {
            return Err(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            });
        }
        let entry = self.buffer.entry((chunk.slot, chunk.from)).or_default();
        *entry
            .get_mut(usize::from(chunk_index))
            .ok_or(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            })? = Some(chunk);
        // If all chunks are already received, reconstruct and store
        // the duplicate slot proof in blockstore
        if entry.iter().flatten().count() == usize::from(num_chunks) {
            let chunks = std::mem::take(entry).into_iter().flatten();
            let slot_leader = self
                .leader_schedule_cache
                .slot_leader_at(slot, /*bank:*/ None)
                .ok_or(Error::UnknownSlotLeader(slot))?;
            let (shred1, shred2) =
                duplicate_shred::into_shreds(&slot_leader.id, chunks, self.shred_version)?;
            if !self.blockstore.has_duplicate_shreds_in_slot(slot) {
                self.blockstore.store_duplicate_slot(
                    slot,
                    shred1.into_payload(),
                    shred2.into_payload(),
                )?;

                // Notify duplicate consensus state machine. Drop if channel is over 50% full
                // to avoid blocking replay.
                if self.duplicate_slots_sender.len() * 2
                    < self.duplicate_slots_sender.capacity().unwrap_or(usize::MAX)
                {
                    self.duplicate_slots_sender
                        .try_send(slot)
                        .map_err(|_| Error::DuplicateSlotSenderFailure)?;
                }
            }
            self.consumed.insert(slot, true);
        }
        Ok(())
```
