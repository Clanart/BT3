No rate limiting on subscribe/unsubscribe calls was found in `rpc/src/rpc_pubsub_service.rs`, which supports the finding below.

### Title
Unbounded shared notification queue lets `logsSubscribe`/`logsUnsubscribe` churn from a single client starve all other pubsub notification types - (File: rpc/src/rpc_subscriptions.rs)

### Summary
`logs_subscribe`/`logs_unsubscribe` do not write into a queue dedicated to logs. Every subscribe and unsubscribe (for *any* subscription type, including logs) is funneled into one process-wide, unbounded, single-consumer channel (`notification_sender`/`notification_receiver`) that also carries `Slot`, `SlotUpdate`, `Vote`, `Root`, `Bank`, `Gossip`, and `SignaturesReceived` entries for every other client on the node.

### Finding Description
`RpcSolPubSubImpl::subscribe`/`unsubscribe` call into `SubscriptionControl::subscribe` and the `Drop` impl of `SubscriptionTokenInner`. [1](#0-0) 

On subscribe, `SubscriptionControl::subscribe` sends `NotificationEntry::Subscribed(params, id)` into the shared channel; on drop (i.e., unsubscribe or websocket disconnect), `SubscriptionTokenInner::drop` sends `NotificationEntry::Unsubscribed(params, id)` into the *same* channel: [2](#0-1) [3](#0-2) 

This channel is created as `crossbeam_channel::unbounded()` and drained by a **single** consumer thread inside `RpcSubscriptions::process_notifications`, which sequentially handles `Subscribed`, `Unsubscribed`, `Slot`, `SlotUpdate`, `Vote`, `Root`, `Bank`, `Gossip`, and `SignaturesReceived` entries in one loop: [4](#0-3) [5](#0-4) 

Because the queue is unbounded and single-threaded, any entry that takes non-trivial time to process (or simply arrives faster than the consumer can drain) delays delivery of *every other* notification type for *every other* client — this is exactly the "unrelated queue explosion" described in the invariant.

For `logsSubscribe`/`logsUnsubscribe` specifically, processing a `Subscribed`/`Unsubscribed` entry for `SubscriptionParams::Logs` is more expensive than for other types: `SubscriptionsTracker::subscribe`/`unsubscribe` call `LogsSubscriptionsIndex::add`/`remove`, which call `update_config()` on every single subscribe/unsubscribe: [6](#0-5) 

`update_config()` takes a **write lock** on the Bank's `transaction_log_collector_config`: [7](#0-6) 

That same `RwLock` is **read-locked on every processed transaction batch** in both banking stage (leader) and replay stage (all validators), inside `Bank::collect_logs`, which runs unconditionally for every batch regardless of whether any logs subscription exists: [8](#0-7) 

So a `logsSubscribe`/`logsUnsubscribe` cycle from one unprivileged client does two things simultaneously:
1. Enqueues work items into the single global, unbounded, single-threaded pubsub notification queue that also carries Slot/Vote/Root/Bank/Gossip/Signature notifications for all other clients.
2. Takes a write lock on a `RwLock` that is read-locked on the hot transaction-execution path (`collect_logs`), for every batch of transactions processed by the node.

`max_active_subscriptions`/`SubscriberCountGuard` caps the number of *concurrently held* subscriptions per subscriber, but does **not** rate-limit subscribe/unsubscribe *churn* (subscribe immediately followed by drop/unsubscribe frees the slot instantly, so the cap doesn't stop a tight subscribe→unsubscribe loop): [9](#0-8) 

No rate limiter, per-connection throttling, or backpressure mechanism was found in `rpc/src/rpc_pubsub_service.rs` governing how fast a single connection can call `logsSubscribe`/`logsUnsubscribe`.

### Impact Explanation
A single unprivileged websocket client that repeatedly calls `logsSubscribe` followed by `logsUnsubscribe` (or lets the token drop) as fast as the transport allows can:
- Flood the single unbounded `notification_sender` channel, growing unbounded server memory (each `TimestampedNotificationEntry` is heap-allocated) since there is no bound (`crossbeam_channel::unbounded`).
- Because the channel is drained by exactly one thread that processes entries strictly in order, this delays delivery of `slotSubscribe`/`voteSubscribe`/`rootSubscribe`/`accountSubscribe`/`programSubscribe`/`signatureSubscribe` notifications for every other RPC pubsub client connected to the node — a direct violation of "one request should not explode unrelated internal work queues."
- Contends for the `transaction_log_collector_config` `RwLock` that is also touched on every transaction batch in banking/replay stage, potentially slowing down transaction processing throughput under sustained churn.

This matches the "RPC DoS/Crash" / "single-client low-rate RPC crash/degradation" impact category: a single low-rate client can degrade pubsub service for all other subscribers and grow unbounded memory.

### Likelihood Explanation
Likelihood is moderate-to-high given the following are all true and unguarded:
- `logsSubscribe`/`logsUnsubscribe` are unprivileged, publicly reachable RPC pubsub methods.
- The subscribe/unsubscribe path is not rate-limited beyond the concurrent-subscription cap, which churn trivially bypasses.
- The underlying channel is unbounded, and the consumer is single-threaded and shared across all notification kinds for the whole node.

However, I could not fully verify (due to index/tool limits) whether there are outer-layer protections such as global websocket message-rate limiters, `PubSubConfig` defaults for `notification_threads`/`queue_capacity_items` that might mitigate impact in practice, or connection-level throttling implemented elsewhere in the validator/RPC service bootstrap code (e.g., in `validator/src/commands/run/args/pub_sub_config.rs`) that I did not fully inspect. These would need to be checked in a live/full-repo Devin session to confirm whether any existing mitigation (e.g., max requests per second per connection) already blocks this pattern before concluding this is exploitable as described.

### Recommendation
- Rate-limit or debounce per-connection subscribe/unsubscribe churn (not just concurrent-subscription count) in `SubscriptionControl::subscribe`/unsubscribe path.
- Separate the notification queue by category (e.g., control-plane `Subscribed`/`Unsubscribed` vs. data-plane `Slot`/`Bank`/`Gossip`/etc.) or use bounded channels with backpressure so churn from one subscription type cannot starve delivery of unrelated notification types.
- Consider debouncing `LogsSubscriptionsIndex::update_config()` (e.g., only recompute/write when the resulting `TransactionLogCollectorConfig` actually changes, or coalesce rapid add/remove pairs) to reduce write-lock contention with `Bank::collect_logs` on the hot transaction path.

### Proof of Concept
1. Open one websocket connection to a validator's RPC pubsub endpoint.
2. In a tight loop, send `logsSubscribe` with `RpcTransactionLogsFilter::All` immediately followed by `logsUnsubscribe` for the returned id (or simply drop/reconnect rapidly to trigger `SubscriptionTokenInner::drop`), as fast as the client can serialize/send frames.
3. From a second websocket connection, subscribe to `slotSubscribe`/`voteSubscribe`/`accountSubscribe` and measure notification latency/backlog while step 2 is running.
4. Instrument (or add temporary logging/metrics around) `notification_receiver.len()` in `RpcSubscriptions::process_notifications` and observe queue growth correlating 1:1 with the first client's subscribe/unsubscribe rate, along with increased latency/drops for the second client's notifications.

### Citations

**File:** rpc/src/rpc_pubsub.rs (L380-405)
```rust
    fn subscribe(&self, params: SubscriptionParams) -> Result<SubscriptionId> {
        let token = self
            .subscription_control
            .subscribe(params)
            .map_err(|_| Error {
                code: ErrorCode::InternalError,
                message: "Internal Error: Subscription refused. Node subscription limit reached"
                    .into(),
                data: None,
            })?;
        let id = token.id();
        self.current_subscriptions.insert(id, token);
        Ok(id)
    }

    fn unsubscribe(&self, id: SubscriptionId) -> Result<bool> {
        if self.current_subscriptions.remove(&id).is_some() {
            Ok(true)
        } else {
            Err(Error {
                code: ErrorCode::InvalidParams,
                message: "Invalid subscription id.".into(),
                data: None,
            })
        }
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L261-269)
```rust
            DashEntry::Vacant(entry) => {
                let id = SubscriptionId::from(self.0.next_id.fetch_add(1, Ordering::AcqRel));
                let inner = create_inner(id, entry.key().clone());
                let weak_ref = WeakSubscriptionTokenRef(Arc::downgrade(&inner), id);
                let _ = self
                    .0
                    .sender
                    .send(NotificationEntry::Subscribed(inner.params.clone(), id).into());
                entry.insert(weak_ref);
```

**File:** rpc/src/rpc_subscription_tracker.rs (L380-407)
```rust
impl LogsSubscriptionsIndex {
    fn add(&mut self, params: &LogsSubscriptionParams) {
        match params.kind {
            LogsSubscriptionKind::All => self.all_count += 1,
            LogsSubscriptionKind::AllWithVotes => self.all_with_votes_count += 1,
            LogsSubscriptionKind::Single(key) => {
                *self.single_count.entry(key).or_default() += 1;
            }
        }
        self.update_config();
    }

    fn remove(&mut self, params: &LogsSubscriptionParams) {
        match params.kind {
            LogsSubscriptionKind::All => self.all_count -= 1,
            LogsSubscriptionKind::AllWithVotes => self.all_with_votes_count -= 1,
            LogsSubscriptionKind::Single(key) => match self.single_count.entry(key) {
                Entry::Occupied(mut entry) => {
                    *entry.get_mut() -= 1;
                    if *entry.get() == 0 {
                        entry.remove();
                    }
                }
                Entry::Vacant(_) => error!("missing entry in single_count"),
            },
        }
        self.update_config();
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L409-436)
```rust
    fn update_config(&self) {
        let mentioned_addresses = self.single_count.keys().copied().collect();
        let config = if self.all_with_votes_count > 0 {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::AllWithVotes,
                mentioned_addresses,
            }
        } else if self.all_count > 0 {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::All,
                mentioned_addresses,
            }
        } else {
            TransactionLogCollectorConfig {
                filter: TransactionLogCollectorFilter::OnlyMentionedAddresses,
                mentioned_addresses,
            }
        };

        *self
            .bank_forks
            .read()
            .unwrap()
            .root_bank()
            .transaction_log_collector_config
            .write()
            .unwrap() = config;
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L568-591)
```rust
impl Drop for SubscriptionTokenInner {
    fn drop(&mut self) {
        match self.control.subscriptions.entry(self.params.clone()) {
            DashEntry::Vacant(_) => {
                warn!("Subscriptions inconsistency (missing entry in by_params)");
            }
            // Check the strong refs count to ensure no other thread recreated this subscription (not token)
            // while we were acquiring the lock.
            DashEntry::Occupied(entry) if entry.get().0.strong_count() == 0 => {
                let _ = self
                    .control
                    .sender
                    .send(NotificationEntry::Unsubscribed(self.params.clone(), self.id).into());
                entry.remove();
                datapoint_info!(
                    "rpc-subscription",
                    ("total", self.control.subscriptions.len(), i64)
                );
            }
            // This branch handles the case in which this entry got recreated
            // while we were waiting for the lock (inside the `DashMap::entry` method).
            DashEntry::Occupied(_entry) /* if _entry.get().0.strong_count() > 0 */ => (),
        }
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L594-617)
```rust
// RAII guard for one subscriber slot. The slot is reserved atomically against
// the configured cap by `try_reserve` (returns `None` if the cap is reached)
// and released on drop. Intentionally not `Clone`: each holder must go through
// `try_reserve` so duplicates cannot bypass the cap.
struct SubscriberCountGuard(Arc<SubscriptionControlInner>);

impl SubscriberCountGuard {
    fn try_reserve(control: &Arc<SubscriptionControlInner>) -> Option<Self> {
        let max = control.max_active_subscriptions;
        control
            .subscriber_count
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |current| {
                (current < max).then_some(current + 1)
            })
            .ok()?;
        Some(Self(Arc::clone(control)))
    }
}

impl Drop for SubscriberCountGuard {
    fn drop(&mut self) {
        self.0.subscriber_count.fetch_sub(1, Ordering::Relaxed);
    }
}
```

**File:** rpc/src/rpc_subscriptions.rs (L600-653)
```rust
    pub fn new_with_config(
        exit: Arc<AtomicBool>,
        max_complete_transaction_status_slot: Arc<AtomicU64>,
        blockstore: Arc<Blockstore>,
        bank_forks: Arc<RwLock<BankForks>>,
        block_commitment_cache: Arc<RwLock<BlockCommitmentCache>>,
        optimistically_confirmed_bank: Arc<RwLock<OptimisticallyConfirmedBank>>,
        config: &PubSubConfig,
        rpc_notifier_ready: Option<Arc<AtomicBool>>,
    ) -> Self {
        let (notification_sender, notification_receiver) = crossbeam_channel::unbounded();

        let subscriptions = SubscriptionsTracker::new(bank_forks.clone());

        let (broadcast_sender, _) = broadcast::channel(config.queue_capacity_items);

        let notifier = RpcNotifier {
            sender: broadcast_sender.clone(),
            recent_items: Mutex::new(RecentItems::new(
                config.queue_capacity_items,
                config.queue_capacity_bytes,
            )),
        };

        let t_cleanup = config.notification_threads.map(|notification_threads| {
            let exit = exit.clone();
            Builder::new()
                .name("solRpcNotifier".to_string())
                .spawn(move || {
                    let pool = rayon::ThreadPoolBuilder::new()
                        .num_threads(notification_threads.get())
                        .thread_name(|i| format!("solRpcNotify{i:02}"))
                        .build()
                        .unwrap();
                    pool.install(|| {
                        if let Some(rpc_notifier_ready) = rpc_notifier_ready {
                            rpc_notifier_ready.fetch_or(true, Ordering::Relaxed);
                        }
                        Self::process_notifications(
                            exit,
                            max_complete_transaction_status_slot,
                            blockstore,
                            notifier,
                            notification_receiver,
                            subscriptions,
                            bank_forks,
                            block_commitment_cache,
                            optimistically_confirmed_bank,
                        )
                    });
                })
                .unwrap()
        });

```

**File:** rpc/src/rpc_subscriptions.rs (L763-905)
```rust
        loop {
            if exit.load(Ordering::Relaxed) {
                break;
            }
            match notification_receiver.recv_timeout(Duration::from_millis(RECEIVE_DELAY_MILLIS)) {
                Ok(notification_entry) => {
                    let TimestampedNotificationEntry { entry, queued_at } = notification_entry;
                    match entry {
                        NotificationEntry::Subscribed(params, id) => {
                            subscriptions.subscribe(params.clone(), id, || {
                                initial_last_notified_slot(
                                    &params,
                                    &bank_forks,
                                    &block_commitment_cache,
                                    &optimistically_confirmed_bank,
                                )
                                .unwrap_or(0)
                            });
                        }
                        NotificationEntry::Unsubscribed(params, id) => {
                            subscriptions.unsubscribe(params, id);
                        }
                        NotificationEntry::Slot(slot_info) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Slot)
                            {
                                debug!("slot notify: {slot_info:?}");
                                stats.notify_slot_count += 1;
                                notifier.notify(slot_info, sub, false);
                            }
                        }
                        NotificationEntry::SlotUpdate(slot_update) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::SlotsUpdates)
                            {
                                debug!("slot update notify: {slot_update:?}");
                                stats.notify_slot_update_count += 1;
                                notifier.notify(slot_update, sub, false);
                            }
                        }
                        // These notifications are only triggered by votes observed on gossip,
                        // unlike `NotificationEntry::Gossip`, which also accounts for slots seen
                        // in VoteState's from bank states built in ReplayStage.
                        NotificationEntry::Vote((vote_pubkey, ref vote_info, signature)) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Vote)
                            {
                                let rpc_vote = RpcVote {
                                    vote_pubkey: vote_pubkey.to_string(),
                                    slots: vote_info.slots(),
                                    hash: bs58::encode(vote_info.hash()).into_string(),
                                    timestamp: vote_info.timestamp(),
                                    signature: signature.to_string(),
                                };
                                debug!("vote notify: {vote_info:?}");
                                stats.notify_vote_count += 1;
                                notifier.notify(&rpc_vote, sub, false);
                            }
                        }
                        NotificationEntry::Root(root) => {
                            if let Some(sub) = subscriptions
                                .node_progress_watchers()
                                .get(&SubscriptionParams::Root)
                            {
                                debug!("root notify: {root:?}");
                                stats.notify_root_count += 1;
                                notifier.notify(root, sub, false);
                            }
                        }
                        NotificationEntry::Bank(commitment_slots) => {
                            const SOURCE: &str = "bank";
                            RpcSubscriptions::notify_watchers(
                                max_complete_transaction_status_slot.clone(),
                                subscriptions.commitment_watchers(),
                                &bank_forks,
                                &blockstore,
                                &commitment_slots,
                                &notifier,
                                SOURCE,
                            );
                        }
                        NotificationEntry::Gossip(slot) => {
                            let commitment_slots = CommitmentSlots {
                                highest_confirmed_slot: slot,
                                ..CommitmentSlots::default()
                            };
                            const SOURCE: &str = "gossip";
                            RpcSubscriptions::notify_watchers(
                                max_complete_transaction_status_slot.clone(),
                                subscriptions.gossip_watchers(),
                                &bank_forks,
                                &blockstore,
                                &commitment_slots,
                                &notifier,
                                SOURCE,
                            );
                        }
                        NotificationEntry::SignaturesReceived((slot, slot_signatures)) => {
                            for slot_signature in &slot_signatures {
                                if let Some(subs) = subscriptions.by_signature().get(slot_signature)
                                {
                                    for subscription in subs.values() {
                                        if let SubscriptionParams::Signature(params) =
                                            subscription.params()
                                        {
                                            if params.enable_received_notification {
                                                stats.notify_signature_count += 1;
                                                notifier.notify(
                                                    RpcResponse::from(RpcNotificationResponse {
                                                        context: RpcNotificationContext { slot },
                                                        value: RpcSignatureResult::ReceivedSignature(
                                                            ReceivedSignatureResult::ReceivedSignature,
                                                        ),
                                                    }),
                                                    subscription,
                                                    false,
                                                );
                                            }
                                        } else {
                                            error!("invalid params type in visit_by_signature");
                                        }
                                    }
                                }
                            }
                        }
                    }
                    stats.notification_entry_processing_time_us +=
                        queued_at.elapsed().as_micros() as u64;
                    stats.notification_entry_processing_count += 1;
                }
                Err(RecvTimeoutError::Timeout) => {
                    // not a problem - try reading again
                }
                Err(RecvTimeoutError::Disconnected) => {
                    warn!("RPC Notification thread - sender disconnected");
                    break;
                }
            }
            stats.maybe_submit();
        }
```

**File:** runtime/src/bank.rs (L4131-4140)
```rust
    fn collect_logs(
        &self,
        transactions: &[impl TransactionWithMeta],
        processing_results: &[TransactionProcessingResult],
    ) {
        let transaction_log_collector_config =
            self.transaction_log_collector_config.read().unwrap();
        if transaction_log_collector_config.filter == TransactionLogCollectorFilter::None {
            return;
        }
```
