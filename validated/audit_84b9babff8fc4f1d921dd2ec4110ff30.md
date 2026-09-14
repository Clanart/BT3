I have enough to confirm this analog. The mechanism is now clear: `get_unspent_lineage_info_for_puzzle_hash` in [1](#0-0)  requires exactly one matching row, and any unprivileged user can create a second row that collides on `(puzzle_hash, amount)`, silently disabling fast-forward for that singleton at admission time in `validate_spend_bundle`.

### Title
Fast-forward singleton lookup requires row uniqueness on `(puzzle_hash, amount)`, letting any user block a singleton's FF spend submission - ([File: chia/full_node/coin_store.py])

### Summary
`CoinStore.get_unspent_lineage_info_for_puzzle_hash` identifies the "latest unspent version" of a fast-forward (FF) eligible singleton purely by `puzzle_hash`, requiring that the query return exactly one row, and additionally that the parent coin have the *same* `puzzle_hash` and `amount` as the unspent coin [2](#0-1) . This is conceptually the same bug class as the reported LineaRollup issue: a value that is not enforced to be globally unique (here, `puzzle_hash` combined with `amount`, rather than a compressed-data hash) is used as the sole lookup key for cardinality of "the" canonical unspent state, and any party who can make that key non-unique can prevent submission/processing of the legitimate item.

### Finding Description
Singleton fast-forward relies on there being exactly one unspent coin for a given `puzzle_hash` whose parent also shares that `puzzle_hash` and `amount`: [3](#0-2) 
If the query returns anything other than exactly one row, the function returns `None` [4](#0-3) , and this is confirmed by the dedicated test case `"More than one unspent with parent that has same puzzlehash and amount"` which explicitly expects `expected_success=False` [5](#0-4) .

`puzzle_hash` is a public value (it's the puzzle hash of the target singleton, needed by anyone who wants to interact with it, e.g. via a DID/NFT/DL/pool singleton or a p2_singleton payment) and `amount` for singletons is commonly a small, easily-guessable/known odd value (frequently `1`). Any unprivileged spend-bundle submitter can create an additional, unrelated coin whose `puzzle_hash` equals the singleton's `puzzle_hash` and whose parent also has that same `puzzle_hash`/`amount` combination — simply by directing a normal `CREATE_COIN` at that puzzle hash with the matching amount from any coin they own, and letting it become "unspent" (or leaving an already-unspent one there). Once two or more such coins exist unspent under the same `puzzle_hash`/`amount` pairing, `get_unspent_lineage_info_for_puzzle_hash` permanently returns `None` for that puzzle hash (until the extra coin is spent).

This return value feeds directly into mempool admission: `validate_spend_bundle` calls it to compute `lineage_info` for FF-eligible spends [6](#0-5) . When `lineage_info` is `None`, the coin's `latest_singleton_lineage` is left `None`, so the spend is treated as `non_ff_spend_ids` — i.e., it falls back to a normal spend rather than being allowed to fast-forward. The same lookup gates whether a rebased FF spend can be advanced across new peaks in `new_peak()`, where a `None` lineage causes eviction of the mempool item entirely: `"this singleton no longer has an unspent coin with this puzzle-hash. FF is not longer available and we just need to evict this mempool item"` [7](#0-6) .

### Impact Explanation
This directly matches the report's bug class: a value that should be a unique identifier for "the" canonical/latest state (in Linea, `dataHash`; here, `(puzzle_hash, amount)` combined with the "exactly one row" constraint) is not enforced to be unique, and any unprivileged party can make it collide. The consequence is a spend-triggered transaction-processing halt: an unrelated third party (not the singleton owner, not a validator) can, with an ordinary spend bundle, durably disable fast-forward eligibility for a targeted singleton's puzzle hash, forcing every attempt to fast-forward-spend that singleton to fall back to (or fail as) a stale, ordinary spend, and causing in-flight FF mempool items for that singleton to be evicted on each new peak once their tracked version is spent and the lookup returns `None`. This can be used to grief DID/NFT/Data-Layer/pooling singleton owners who rely on FF semantics for uninterrupted advancement of their singleton without needing to resubmit fresh spends each time.

### Likelihood Explanation
Likelihood is moderate-to-high in scope terms: it requires only a standard, unsigned coin creation targeting a known public puzzle hash with a matching amount — something any wallet user or spend-bundle submitter can do without any special privilege, key compromise, or network-level position. The harder part is knowing/guessing the singleton's exact `amount` at a given time, but this is knowable for public FF singletons and is exactly why `can_fast_forward_singleton` bases its check on amount, not on some unpredictable extra field.

### Recommendation
Do not rely on `puzzle_hash`-only cardinality plus a same-parent-amount heuristic to identify "the" latest singleton version. Instead: (1) derive/track the FF chain using the singleton's launcher ID (a fixed, per-singleton unique identifier already embedded in singleton puzzles) as the primary key rather than `puzzle_hash`, so that unrelated coins sharing a puzzle hash/amount by coincidence cannot be confused with the singleton's own lineage; and/or (2) when the query in `get_unspent_lineage_info_for_puzzle_hash` yields more than one candidate row, disambiguate using the singleton's actual lineage proof/launcher ID rather than unconditionally returning `None`.

### Proof of Concept
1. Identify a live FF-eligible singleton with public `puzzle_hash = P` and current `amount = A` (odd, as required for FF eligibility).
2. From any owned coin, submit a normal spend bundle containing `CREATE_COIN P A` to create an unrelated coin with the same puzzle hash and amount, and let it confirm as unspent.
3. Submit (or have the singleton owner submit) a fast-forward-eligible spend of the real singleton. In `validate_spend_bundle`, the call to `get_unspent_lineage_info_for_puzzle_hash(P)` in [8](#0-7)  now returns `None` because two unspent rows match `puzzle_hash = P` with `parent.puzzle_hash = P, parent.amount = A` (mirroring the "More than one unspent" test case in `test_coin_store.py`).
4. The singleton's spend is now treated as a plain (non-FF) spend; any subsequent attempt to fast-forward it, or an already-admitted FF mempool item whose tracked version gets spent, will hit the `None`-lineage eviction path in `new_peak()` at [9](#0-8) , permanently blocking fast-forward advancement for that puzzle hash until the attacker's extra coin is spent.

### Citations

**File:** chia/full_node/coin_store.py (L655-655)
```python
    # Lookup the most recent unspent lineage that matches a puzzle hash
```

**File:** chia/full_node/coin_store.py (L656-679)
```python
    async def get_unspent_lineage_info_for_puzzle_hash(self, puzzle_hash: bytes32) -> UnspentLineageInfo | None:
        async with self.db_wrapper.reader_no_transaction() as conn:
            async with conn.execute(
                "SELECT unspent.coin_name, "
                "unspent.coin_parent, "
                "parent.coin_parent "
                "FROM coin_record AS unspent "
                f"INDEXED BY {self._unspent_lineage_for_ph_idx} "
                "LEFT JOIN coin_record AS parent ON unspent.coin_parent = parent.coin_name "
                "WHERE unspent.spent_index = -1 "
                "AND parent.spent_index > 0 "
                "AND unspent.puzzle_hash = ? "
                "AND parent.puzzle_hash = unspent.puzzle_hash "
                "AND parent.amount = unspent.amount",
                (puzzle_hash,),
            ) as cursor:
                rows = list(await cursor.fetchall())
                if len(rows) != 1:
                    log.debug("Expected 1 unspent with puzzle hash %s, but found %s", puzzle_hash.hex(), len(rows))
                    return None
                coin_id, parent_id, parent_parent_id = rows[0]
                return UnspentLineageInfo(
                    coin_id=bytes32(coin_id), parent_id=bytes32(parent_id), parent_parent_id=bytes32(parent_parent_id)
                )
```

**File:** chia/_tests/core/full_node/stores/test_coin_store.py (L790-801)
```python
    UnspentLineageInfoCase(
        id="More than one unspent with parent that has same puzzlehash and amount",
        items=[
            UnspentLineageInfoTestItem(TEST_COIN_ID, TEST_PUZZLEHASH, TEST_AMOUNT, TEST_PARENT_ID),
            UnspentLineageInfoTestItem(b"2" * 32, TEST_PUZZLEHASH, TEST_AMOUNT, TEST_PARENT_ID),
            UnspentLineageInfoTestItem(b"3" * 32, b"3" * 32, 3, b"2" * 32),
            UnspentLineageInfoTestItem(
                TEST_PARENT_ID, TEST_PUZZLEHASH, TEST_AMOUNT, TEST_PARENT_PARENT_ID, spent_index=1
            ),
        ],
        expected_success=False,
    ),
```

**File:** chia/full_node/mempool_manager.py (L728-746)
```python
            lineage_info = None
            if bool(spend_conds.flags & ELIGIBLE_FOR_FF) and supports_fast_forward(coin_spend):
                # Make sure the fast forward spend still has a version that is
                # still unspent, because if the singleton has been spent in a
                # non-FF spend, this fast forward spend will never become valid.
                # So treat this as a normal spend, which requires the exact coin
                # to exist and be unspent.
                # Singletons that were created before the optimization of using
                # spent_index will also fail this test, and such spends will
                # fall back to be treated as non-FF spends.
                lineage_info = await get_unspent_lineage_info_for_puzzle_hash(spend_conds.puzzle_hash)
                if lineage_info is not None and not can_fast_forward_singleton(
                    unspent_lineage_info=lineage_info, coin=coin_spend.coin
                ):
                    # The latest unspent version of this singleton has a
                    # different amount than the coin we're spending, so this
                    # spend can never be fast forwarded onto it. Fall back to
                    # treating it as a normal spend.
                    lineage_info = None
```

**File:** chia/full_node/mempool_manager.py (L1030-1037)
```python
                    lineage_info = await lineage_cache.get_unspent_lineage_info(bcs.coin_spend.coin.puzzle_hash)
                    if lineage_info is None:
                        # this singleton no longer has an unspent coin with
                        # this puzzle-hash. FF is not longer available and we
                        # just need to evict this mempool item
                        self.remove_seen(item_name)
                        spendbundle_ids_to_remove.add(item_name)
                        break
```
