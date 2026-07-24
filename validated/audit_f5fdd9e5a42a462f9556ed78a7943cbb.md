The code evidence is conclusive. Let me trace every step.

**`match_block` returns at most one event per block:** [1](#0-0) 

The `.min()` call selects only the single `(MatcherOrd, event)` pair with the lowest ordering. All other matching events are silently discarded.

**Both `Challenged` and `KickoffFinalizerSpent` produce `MatcherOrd::TxIndex`:** [2](#0-1) 

`SpentUtxo` and `SpentUtxoButNotTxid` both return `MatcherOrd::TxIndex(block_tx_index)`. Whichever spending tx appears at a lower index in the block wins `.min()`.

**Both matchers are registered in `add_default_kickoff_matchers`:** [3](#0-2) 

**`KickoffFinalizerSpent` immediately transitions to `closed`:** [4](#0-3) 

The docstring confirms `closed` clears all matchers: [5](#0-4) 

**`process_with_ctx` calls `match_block` exactly once per block:** [6](#0-5) 

There is no re-matching after the first event is processed. The `Challenged` event is permanently lost.

**`challenged` flag is only set in `on_challenged_entry`:** [7](#0-6) 

All downstream disprove logic gates on `self.challenged`: [8](#0-7) 

---

### Title
Single-event-per-block `match_block` silently drops `Challenged` when `KickoffFinalizerSpent` appears first in the same block — (`core/src/states/kickoff.rs`)

### Summary
`KickoffStateMachine::match_block` uses `.min()` to return at most one event per block. When a Bitcoin block contains both a challenge-UTXO spend (triggering `Challenged`) and a kickoff-finalizer spend (triggering `KickoffFinalizerSpent`), only the event whose spending transaction has the lower block-index is returned. If the kickoff-finalizer spend appears first, `KickoffFinalizerSpent` is processed, the machine transitions to `closed` (clearing all matchers), and `Challenged` is permanently dropped. The `challenged` flag remains `false`, the disprove duty is never dispatched, and the operator escapes slashing.

### Finding Description
`match_block` collects all `(MatcherOrd, event)` pairs that fire for a given block and calls `.min()`, returning a `Vec` of exactly zero or one element:

```rust
// core/src/states/kickoff.rs:143-154
fn match_block(&self, block: &BlockCache) -> Vec<Self::StateEvent> {
    self.matchers
        .iter()
        .filter_map(|(matcher, kickoff_event)| {
            matcher.matches(block).map(|ord| (ord, kickoff_event))
        })
        .min()                                    // ← only the minimum survives
        .map(|(_, kickoff_event)| kickoff_event)
        .into_iter()
        .cloned()
        .collect()
}
```

`process_with_ctx` calls `match_block` once and iterates the returned list:

```rust
// core/src/states/mod.rs:92-103
let events = self.match_block(&block.cache);   // at most 1 event
for event in events {
    self.handle_with_context(&event, &mut ctx).await;
}
```

Both `Challenged` (via `Matcher::SpentUtxoButNotTxid` on the challenge UTXO) and `KickoffFinalizerSpent` (via `Matcher::SpentUtxo` on the kickoff-finalizer UTXO) resolve to `MatcherOrd::TxIndex(block_tx_index)`. When both spending transactions land in the same block, the one with the lower index wins. If the kickoff-finalizer spend is index-lower, `KickoffFinalizerSpent` is the sole returned event, `Challenged` is discarded, and `on_closed_entry` clears all remaining matchers before the next block is processed.

### Impact Explanation
- `self.challenged` stays `false`.
- `on_challenged_entry` never runs; no `TimeToSendWatchtowerChallenge` matcher is inserted.
- `disprove_if_ready` and `send_operator_asserts_if_ready` both gate on `self.challenged`, so `Duty::VerifierDisprove` is never dispatched.
- The operator's slashable collateral UTXO is preserved; the verifier's disprove window is permanently closed.
- This breaks the core safety invariant of the challenge/disprove flow: every challenged kickoff must enter the `challenged` state and trigger the disprove duty.

### Likelihood Explanation
A malicious operator can increase the probability of this race by broadcasting their kickoff-finalizer spend with a fee rate high enough to be mined before the challenge tx in the same block. Bitcoin miners order transactions by fee rate by default, so a sufficiently fee-bumped kickoff-finalizer spend will reliably appear at a lower block index than a standard-fee challenge tx. The operator controls the kickoff-finalizer spend transaction and can time and fee-bump it freely.

### Recommendation
`match_block` must return **all** matching events for a block, not just the minimum. The `.min()` call should be replaced with a collection of all matching events, sorted by `MatcherOrd` so that earlier-in-block events are processed first but none are dropped:

```rust
fn match_block(&self, block: &BlockCache) -> Vec<Self::StateEvent> {
    let mut matched: Vec<(MatcherOrd, &KickoffEvent)> = self.matchers
        .iter()
        .filter_map(|(matcher, event)| {
            matcher.matches(block).map(|ord| (ord, event))
        })
        .collect();
    matched.sort_by(|a, b| a.0.cmp(&b.0));
    matched.into_iter().map(|(_, ev)| ev.clone()).collect()
}
```

The same fix must be applied to `RoundStateMachine::match_block` if it has the same pattern.

### Proof of Concept
1. Build a `BlockCache` containing two transactions: tx at index 0 spends the kickoff-finalizer UTXO; tx at index 1 spends the challenge UTXO (not via the challenge-timeout txid).
2. Initialize a `KickoffStateMachine` in `kickoff_started` with both matchers registered.
3. Call `process_with_ctx` with that block.
4. Assert: the machine is now in `closed`, `self.challenged == false`, and no `Duty::VerifierDisprove` was dispatched.
5. Swap the tx indices (challenge at 0, finalizer at 1) and repeat — now `challenged == true` and the disprove duty fires, confirming the ordering dependency.

### Citations

**File:** core/src/states/kickoff.rs (L93-95)
```rust
/// - It tracks the progress of the kickoff, including challenges, operator actions, and finalization.
/// - When terminal events occur (e.g., finalizer or burn connector spent), the state machine transitions to `closed` and clears all matchers.
/// - The state machine interacts with the owner to perform protocol duties (e.g., sending challenges, asserts, or disproves) as required by the protocol logic.
```

**File:** core/src/states/kickoff.rs (L143-154)
```rust
    fn match_block(&self, block: &BlockCache) -> Vec<Self::StateEvent> {
        self.matchers
            .iter()
            .filter_map(|(matcher, kickoff_event)| {
                matcher.matches(block).map(|ord| (ord, kickoff_event))
            })
            .min()
            .map(|(_, kickoff_event)| kickoff_event)
            .into_iter()
            .cloned()
            .collect()
    }
```

**File:** core/src/states/kickoff.rs (L261-270)
```rust
        if self.challenged && self.operator_asserts.len() == ClementineBitVMPublicKeys::number_of_assert_txs()
            && self.latest_blockhash != Witness::default()
            && self.spent_watchtower_utxos.len() == self.deposit_data.get_num_watchtowers()
            // check if all operator acks are received, one ack for each watchtower challenge
            // to make sure we have all preimages required to disprove if operator didn't include 
            // the watchtower challenge in the BitVM proof
            && self.watchtower_challenges.keys().all(|idx| self.operator_challenge_acks.contains_key(idx))
        {
            self.send_disprove(context).await;
        }
```

**File:** core/src/states/kickoff.rs (L376-377)
```rust
    pub(crate) async fn on_challenged_entry(&mut self, context: &mut StateContext<T>) {
        self.challenged = true;
```

**File:** core/src/states/kickoff.rs (L523-526)
```rust
            KickoffEvent::KickoffFinalizerSpent => {
                tracing::info!("Detected kickoff finalizer spent for {}", self.kickoff_data,);
                Transition(State::closed())
            }
```

**File:** core/src/states/kickoff.rs (L763-784)
```rust
        // add kickoff finalizer utxo spent matcher
        self.matchers.insert(
            Matcher::SpentUtxo(OutPoint {
                txid: kickoff_txid,
                vout: UtxoVout::KickoffFinalizer.get_vout(),
            }),
            KickoffEvent::KickoffFinalizerSpent,
        );
        // add challenge detector matcher, if challenge utxo is not spent by challenge timeout tx, it means the kickoff is challenged
        let challenge_timeout_txhandler =
            remove_txhandler_from_map(&mut txhandlers, TransactionType::ChallengeTimeout)?;
        let challenge_timeout_txid = challenge_timeout_txhandler.get_txid();
        self.matchers.insert(
            Matcher::SpentUtxoButNotTxid(
                OutPoint {
                    txid: kickoff_txid,
                    vout: UtxoVout::Challenge.get_vout(),
                },
                vec![*challenge_timeout_txid],
            ),
            KickoffEvent::Challenged,
        );
```

**File:** core/src/states/matcher.rs (L62-83)
```rust
    pub fn matches(&self, block: &BlockCache) -> Option<MatcherOrd> {
        match self {
            Matcher::SentTx(txid) if block.contains_txid(txid) => Some(MatcherOrd::TxIndex(
                *block.txids.get(txid).expect("txid is in cache"),
            )),
            Matcher::SpentUtxo(outpoint) if (block.is_utxo_spent(outpoint)) => Some(
                MatcherOrd::TxIndex(*block.spent_utxos.get(outpoint).expect("utxo is in cache")),
            ),
            Matcher::BlockHeight(height) if *height <= block.block_height => {
                Some(MatcherOrd::BlockHeight)
            }
            Matcher::SpentUtxoButNotTxid(outpoint, txids)
                if block.is_utxo_spent(outpoint)
                    && !txids.iter().any(|txid| block.contains_txid(txid)) =>
            {
                Some(MatcherOrd::TxIndex(
                    *block.spent_utxos.get(outpoint).expect("utxo is in cache"),
                ))
            }
            _ => None,
        }
    }
```

**File:** core/src/states/mod.rs (L92-103)
```rust
        let events = self.match_block(&block.cache);
        if events.is_empty() {
            ContextProcessResult::Unchanged(self)
        } else {
            let mut ctx = block.clone();
            ContextProcessResult::Processing(async move {
                for event in events {
                    self.handle_with_context(&event, &mut ctx).await;
                }
                (self, ctx)
            })
        }
```
