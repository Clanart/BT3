## Finding: blockSubscribe admission control caps subscription *count* globally, not per‑subscription downstream cost, allowing a single client to multiply blockstore reads and encoding work per slot — (File: `rpc/src/rpc_pubsub.rs`, `rpc/src/rpc_subscriptions.rs`)

### Summary
`block_subscribe` admission is gated only by a single global counter (`max_active_subscriptions`, default `1_000_000`) enforced in `SubscriptionControl::subscribe`, called from `RpcSolPubSubImpl::subscribe` [1](#0-0) . There is no per-connection limit on the number of `blockSubscribe` subscriptions, and no cost-weighting of admission based on the `RpcBlockSubscribeConfig` (`transaction_details`, `encoding`, `show_rewards`, `max_supported_transaction_version`) a client requests [2](#0-1) . Because the notifier processes work per *subscriber id* rather than per unique filter/params, one client that opens many `Block` subscriptions (even with identical `All` filters) forces the node to perform that many independent `blockstore.get_complete_block()` reads plus filtering/serialization on every confirmed/finalized slot.

### Finding Description
The subscription admission check is a flat counter test: [3](#0-2) 
This test itself documents that duplicate params still each consume a separate subscriber slot ("Cap of 1 must reject a second subscriber even when params match an existing subscription"), confirming that de-duplication by params is *not* performed at the admission or notification stage.

Downstream, `notify_watchers` iterates the `HashMap<SubscriptionId, Arc<SubscriptionInfo>>` of block watchers in parallel, and for every distinct `SubscriptionId` — regardless of whether its `BlockSubscriptionParams` match another subscriber's — it independently calls `blockstore.get_complete_block()` and `filter_block_result_txs()`, then serializes and pushes a full block payload through `RpcNotifier::notify`: [4](#0-3) 

`RpcNotifier::notify` allocates and serializes a fresh JSON string per call and pushes it to a shared broadcast channel plus a `RecentItems` retention buffer: [5](#0-4) 

Because the only gate on subscription creation is the flat global counter (default 1,000,000) with no per-connection or per-client sub-limit and no cost weighting by config (e.g., `transaction_details: Full`, `encoding: Base64`, `show_rewards: true`, all legal, unprivileged parameters), a single client can create a large number of `Block` subscriptions on one WebSocket connection well under the global cap. Each additional subscription linearly multiplies the per-slot blockstore I/O, filtering, and JSON serialization cost the `solRpcNotifier` thread pool must perform on every slot — the admission control bounds *ingress* (how many `blockSubscribe` calls are accepted) but does not bound the *aggregate downstream work* those admitted subscriptions cause per slot.

Separately, the broadcast channel and `RecentItems` structure are correctly bounded (`queue_capacity_items` / `queue_capacity_bytes`), and a slow client causes a `broadcast::error::RecvError::Lagged`, which the connection handler treats as a hard error and disconnects that client [6](#0-5) [7](#0-6) . So the pure "slow reader accumulates unbounded memory" vector is mitigated. The exploitable gap is specifically the **per-subscriber (not per-params) fan-out of blockstore fetch + serialize work**, which is not capped by the admission-time subscription counter.

### Impact Explanation
A single unprivileged client, using only public `blockSubscribe` parameters, can open many block subscriptions on one connection (bounded only by the very large global `max_active_subscriptions`) with maximally expensive configs (`transaction_details: Full`, `show_rewards: true`, base64/legacy encoding to avoid any cheaper paths). On every slot notification this forces the notifier thread pool to perform that many independent blockstore reads and full-block JSON serializations, which is CPU/I/O work multiplied by subscription count rather than bounded by a single "cost budget" per client. This matches the "RPC DoS/Crash" impact category: node CPU/I/O for the notification thread pool can be driven to saturation by one client's admitted-but-uncosted subscriptions, degrading or crashing pubsub service for all clients.

### Likelihood Explanation
Moderate-to-high: exploiting this requires no privileged access — only issuing repeated `blockSubscribe` JSON-RPC calls over a single WebSocket connection with `enable_block_subscription` turned on (an opt-in, "unstable" feature flag per `rpc-pubsub-enable-block-subscription` in `validator/src/commands/run/args/pub_sub_config.rs`) [8](#0-7) . Because `enable_block_subscription` defaults to disabled and must be explicitly enabled by the operator, exploitability depends on the validator being configured with this unstable flag on, which limits real-world exposure but does not eliminate the underlying design gap when it is enabled.

### Recommendation
- Track and cap per-connection subscription counts (not just the node-wide total) for expensive subscription kinds like `Block`.
- De-duplicate identical `BlockSubscriptionParams` at the notifier level so that multiple subscribers with the same filter share one blockstore fetch/serialize/encode result.
- Introduce a cost-weighted admission limit for `Block` subscriptions that accounts for `transaction_details`, `encoding`, and `show_rewards`, rather than treating every subscription as equal-cost against the flat counter.

### Proof of Concept
1. Start a validator with `--rpc-pubsub-enable-block-subscription`.
2. From one WebSocket client connection, issue `blockSubscribe` repeatedly (e.g., thousands of times) with `{"filter":"all","config":{"commitment":"confirmed","transactionDetails":"full","encoding":"base64","showRewards":true}}`, each call returning a new distinct `SubscriptionId` (per `RpcSolPubSubImpl::subscribe` / `SubscriptionControl::subscribe`) [1](#0-0) .
3. Observe that on every subsequent slot notification, the `solRpcNotifier` thread pool performs one `blockstore.get_complete_block()` + `filter_block_result_txs()` + JSON serialization *per subscription*, i.e., work scales linearly with the number of subscriptions opened by this single connection [4](#0-3) .
4. Measure CPU/I/O saturation of the `solRpcNotifier`/rayon notification pool as subscription count grows, demonstrating that admission (a flat count check) does not bound the aggregate downstream per-slot work triggered by one client.

### Citations

**File:** rpc/src/rpc_pubsub.rs (L380-393)
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
```

**File:** rpc/src/rpc_pubsub.rs (L545-573)
```rust
    fn block_subscribe(
        &self,
        filter: RpcBlockSubscribeFilter,
        config: Option<RpcBlockSubscribeConfig>,
    ) -> Result<SubscriptionId> {
        if !self.config.enable_block_subscription {
            return Err(Error::new(jsonrpc_core::ErrorCode::MethodNotFound));
        }
        let config = config.unwrap_or_default();
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;
        let params = BlockSubscriptionParams {
            commitment: config.commitment.unwrap_or_default(),
            encoding: config.encoding.unwrap_or(UiTransactionEncoding::Base64),
            kind: match filter {
                RpcBlockSubscribeFilter::All => BlockSubscriptionKind::All,
                RpcBlockSubscribeFilter::MentionsAccountOrProgram(key) => {
                    BlockSubscriptionKind::MentionsAccountOrProgram(param::<Pubkey>(
                        &key,
                        "mentions_account_or_program",
                    )?)
                }
            },
            transaction_details: config.transaction_details.unwrap_or_default(),
            show_rewards: config.show_rewards.unwrap_or_default(),
            max_supported_transaction_version: config.max_supported_transaction_version,
        };
        self.subscribe(SubscriptionParams::Block(params))
    }
```

**File:** rpc/src/rpc_subscription_tracker.rs (L735-759)
```rust
    #[test]
    fn duplicate_params_consume_separate_subscriber_slots() {
        // Cap of 1 must reject a second subscriber even when params match an
        // existing subscription (GHSA: duplicate-params cap bypass).
        let (sender, _receiver) = bounded(1024);
        let (broadcast_sender, _broadcast_receiver) = broadcast::channel(42);
        let control = SubscriptionControl::new(1, sender, broadcast_sender);

        let token1 = control.subscribe(SubscriptionParams::Slot).unwrap();
        assert_eq!(control.total(), 1);
        assert_eq!(control.subscriber_total(), 1);

        assert!(matches!(
            control.subscribe(SubscriptionParams::Slot),
            Err(Error::TooManySubscriptions)
        ));
        assert_eq!(control.subscriber_total(), 1);

        drop(token1);
        // After the only holder drops, a fresh subscriber should fit again.
        let token2 = control.subscribe(SubscriptionParams::Slot).unwrap();
        assert_eq!(control.subscriber_total(), 1);
        drop(token2);
        assert_eq!(control.subscriber_total(), 0);
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L289-322)
```rust
impl RpcNotifier {
    fn notify<T>(&self, value: T, subscription: &SubscriptionInfo, is_final: bool)
    where
        T: serde::Serialize,
    {
        let buf_arc = RPC_NOTIFIER_BUF.with(|buf| {
            let mut buf = buf.borrow_mut();
            buf.clear();
            let notification = Notification {
                jsonrpc: Some(jsonrpc_core::Version::V2),
                method: subscription.method(),
                params: NotificationParams {
                    result: value,
                    subscription: subscription.id(),
                },
            };
            serde_json::to_writer(Cursor::new(&mut *buf), &notification)
                .expect("serialization never fails");
            let buf_str = str::from_utf8(&buf).expect("json is always utf-8");
            Arc::new(String::from(buf_str))
        });

        let notification = RpcNotification {
            subscription_id: subscription.id(),
            json: Arc::downgrade(&buf_arc),
            is_final,
            created_at: Instant::now(),
        };
        // There is an unlikely case where this can fail: if the last subscription is closed
        // just as the notifier generates a notification for it.
        let _ = self.sender.send(notification);

        self.recent_items.lock().unwrap().push(buf_arc);
    }
```

**File:** rpc/src/rpc_subscriptions.rs (L968-1030)
```rust
                SubscriptionParams::Block(params) => {
                    num_blocks_found.fetch_add(1, Ordering::Relaxed);
                    if let Some(slot) = slot {
                        let bank = bank_forks.read().unwrap().get(slot);
                        if let Some(bank) = bank {
                            // We're calling it unnotified in this context
                            // because, logically, it gets set to `last_notified_slot + 1`
                            // on the final iteration of the loop down below.
                            // This is used to notify blocks for slots that were
                            // potentially missed due to upstream transient errors
                            // that led to this notification not being triggered for
                            // a slot.
                            //
                            // e.g.
                            // notify_watchers is triggered for Slot 1
                            // some time passes
                            // notify_watchers is triggered for Slot 4
                            // this will try to fetch blocks for slots 2, 3, and 4
                            // as long as they are ancestors of `slot`
                            let mut w_last_unnotified_slot =
                                subscription.last_notified_slot.write().unwrap();
                            // would mean it's the first notification for this subscription connection
                            if *w_last_unnotified_slot == 0 {
                                *w_last_unnotified_slot = slot;
                            }
                            let mut slots_to_notify: Vec<_> =
                                (*w_last_unnotified_slot..slot).collect();
                            let ancestors = bank.proper_ancestors_set();
                            slots_to_notify.retain(|slot| ancestors.contains(slot));
                            slots_to_notify.push(slot);
                            for s in slots_to_notify {
                                // To avoid skipping a slot that fails this condition,
                                // caused by non-deterministic concurrency accesses, we
                                // break out of the loop. Besides if the current `s` is
                                // greater, then any `s + K` is also greater.
                                if s > max_complete_transaction_status_slot.load(Ordering::SeqCst) {
                                    break;
                                }

                                let block_update_result = blockstore
                                    .get_complete_block(s, false)
                                    .map_err(|e| {
                                        error!("get_complete_block error: {e}");
                                        RpcBlockUpdateError::BlockStoreError
                                    })
                                    .and_then(|block| filter_block_result_txs(block, s, params));

                                match block_update_result {
                                    Ok(block_update) => {
                                        if let Some(block_update) = block_update {
                                            notifier.notify(
                                                RpcResponse::from(RpcNotificationResponse {
                                                    context: RpcNotificationContext { slot: s },
                                                    value: block_update,
                                                }),
                                                subscription,
                                                false,
                                            );
                                            num_blocks_notified.fetch_add(1, Ordering::Relaxed);
                                            // the next time this subscription is notified it will
                                            // try to fetch all slots between (s + 1) to `slot`, inclusively
                                            *w_last_unnotified_slot = s + 1;
                                        }
```

**File:** rpc/src/rpc_pubsub_service.rs (L337-347)
```rust
#[derive(Debug, Error)]
enum Error {
    #[error("handshake error: {0}")]
    Handshake(#[from] soketto::handshake::Error),
    #[error("connection error: {0}")]
    Connection(#[from] soketto::connection::Error),
    #[error("broadcast queue error: {0}")]
    Broadcast(#[from] broadcast::error::RecvError),
    #[error("client has lagged behind (notification is gone)")]
    NotificationIsGone,
}
```

**File:** rpc/src/rpc_pubsub_service.rs (L399-405)
```rust
                    result = broadcast_receiver.recv() => {

                        // In both possible error cases (closed or lagged) we disconnect the client.
                        if let Some(json) = broadcast_handler.handle(result?)? {
                            sender.send_text(&*json).await?;
                        }
                    },
```

**File:** validator/src/commands/run/args/pub_sub_config.rs (L72-76)
```rust
        Arg::with_name("rpc_pubsub_enable_block_subscription")
            .long("rpc-pubsub-enable-block-subscription")
            .requires("enable_rpc_transaction_history")
            .takes_value(false)
            .help("Enable the unstable RPC PubSub `blockSubscribe` subscription"),
```
