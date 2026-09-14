### Title
Missing URL length validation before uint16 length-prefix encoding of on-chain DL mirror URLs — ([File: chia/data_layer/dl_wallet_store.py])

### Summary
The CVE describes NSD trusting an attacker-supplied RR size that exceeds the range of the `uint16_t` used to frame it, corrupting the length field and causing an out-of-bounds write during re-serialization. The Data Layer wallet's mirror-URL persistence path has the analogous defect: attacker-controlled, on-chain URL bytes are framed with a fixed 2-byte (`uint16`) length prefix with no upstream bound check on the URL length, and the same fixed-width value is later trusted verbatim to re-slice the packed blob.

### Finding Description
Any peer can create a "mirror" coin for a Data Layer singleton by spending to the mirror puzzle with a memo list `[launcher_id, *urls]`. When a tracking wallet observes this coin, `DataLayerWallet.coin_added` extracts the URLs straight from the untrusted spend's memos via `get_mirror_info(...)` and decodes them with no length restriction: [1](#0-0) 

These attacker-controlled `urls` are then persisted by `DataLayerStore.add_mirror`, which frames each URL with a `uint16` length prefix before concatenating into a single blob column: [2](#0-1) 

`uint16(len(url))` is computed directly from the attacker-supplied byte length with no prior validation that `len(url) <= 0xFFFF`. This is structurally identical to the NSD bug: an untrusted, unbounded input size is fed into a fixed-width length field used purely for downstream framing/reconstruction, with no size check performed before the narrowing conversion.

On the read side, `_row_to_mirror` blindly trusts the embedded 2-byte length to re-slice the packed blob back into individual URLs: [3](#0-2) 

If the encoding step ever produces a length value that does not match the true byte length of the URL that follows it (whether because the underlying `uint16` constructor throws on overflow, or — depending on the exact `chia_rs` binding behavior, which this index cannot fully verify — silently truncates), the length-prefixed framing invariant that `_row_to_mirror`'s parser depends on is broken, exactly mirroring how NSD's corrupted RR-size field desynchronized its AXFR record parser.

### Impact Explanation
This is reachable by any wallet tracking a Data Layer singleton, triggered purely by an on-chain coin created by an unprivileged party (no special permissions, network position, or leaked keys needed) — it fits the "Data Layer client" and "wallet state" reachability categories in scope. If the fixed-width conversion raises, the exception occurs inside the wallet's per-coin sync callback (`coin_added`), which is invoked for every newly observed coin during standard sync; an uncaught exception here is a spend-triggered halt of wallet coin/transaction processing for the affected wallet. If instead the value silently wraps (dependency-dependent, unverifiable from this index), the length-prefixed blob becomes internally inconsistent and the read-side parser (`_row_to_mirror`) will desynchronize and either raise, return corrupted URLs, or misattribute byte ranges across mirror entries stored in the same blob.

### Likelihood Explanation
Triggering the condition only requires broadcasting a standard mirror-creation spend with a memo string exceeding 65535 bytes attached to it — well within reach of any wallet user or Data Layer client, and not gated by consensus rules since memo content isn't otherwise length-restricted by this specific code path. However, I could not confirm within this index whether the `chia_rs`-backed `uint16` constructor raises `OverflowError`/`ValueError` on out-of-range input or silently truncates, since `chia_rs.sized_ints` is a compiled dependency not present in this repository's indexed sources. That uncertainty affects whether the concrete outcome is a processing halt (exception) or data corruption (silent truncation), though both fall under permitted impact categories in the validation rubric.

### Recommendation
Validate `len(url) <= 0xFFFF` (or an even smaller sane practical URL limit) before constructing the `uint16` length prefix in `DataLayerStore.add_mirror`, rejecting or truncating oversized URLs with an explicit, handled error rather than allowing an unvalidated attacker-controlled size to reach the fixed-width encoding step. Additionally, harden `_row_to_mirror` to validate that the parsed length does not exceed the remaining buffer, mirroring the defensive bounds checks already used elsewhere in this codebase (e.g. `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list` helpers).

### Proof of Concept
1. As any wallet user, spend a coin to the Data Layer mirror puzzle for a `launcher_id`, including a memo URL string longer than 65535 bytes (`memos=[[launcher_id, b"A" * 70000]]`), per the flow in `DataLayerWallet.create_new_mirror`.
2. Have a wallet tracking that `launcher_id` sync the block containing this coin; `coin_added` will call `get_mirror_info` and pass the oversized URL bytes into `dl_store.add_mirror`.
3. Observe that `uint16(len(url))` in `DataLayerStore.add_mirror` either raises inside the wallet's coin-sync callback (halting further coin processing) or, depending on the exact `chia_rs` conversion semantics, silently produces a mismatched length prefix that corrupts the packed `urls` blob, which is then misread by `_row_to_mirror` on the next `get_mirrors_for_launcher` call.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L775-799)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        if coin.puzzle_hash == create_mirror_puzzle().get_tree_hash():
            parent_state: CoinState = (
                await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            )[0]
            parent_spend = await fetch_coin_spend(height, parent_state.coin, peer)
            assert parent_spend is not None
            launcher_id, urls = get_mirror_info(parent_spend.puzzle_reveal, parent_spend.solution)
            # Don't track mirrors with empty url list.
            if not urls:
                return
            if await self.wallet_state_manager.dl_store.is_launcher_tracked(launcher_id):
                ours: bool = await self.wallet_state_manager.get_wallet_for_coin(coin.parent_coin_info) is not None
                await self.wallet_state_manager.dl_store.add_mirror(
                    Mirror(
                        coin.name(),
                        launcher_id,
                        uint64(coin.amount),
                        Mirror.decode_urls(urls),
                        ours,
                        height,
                    )
                )
```

**File:** chia/data_layer/dl_wallet_store.py (L31-46)
```python
def _row_to_mirror(row: Row, confirmed_at_height: uint32 | None) -> Mirror:
    urls: list[bytes] = []
    byte_list: bytes = row[3]
    while byte_list != b"":
        length = uint16.from_bytes(byte_list[0:2])
        url = byte_list[2 : length + 2]
        byte_list = byte_list[length + 2 :]
        urls.append(url)
    return Mirror(
        bytes32(row[0]),
        bytes32(row[1]),
        uint64.from_bytes(row[2]),
        Mirror.decode_urls(urls),
        bool(row[4]),
        confirmed_at_height,
    )
```

**File:** chia/data_layer/dl_wallet_store.py (L308-325)
```python
    async def add_mirror(self, mirror: Mirror) -> None:
        """
        Add a mirror coin to the DB
        """

        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "INSERT OR REPLACE INTO mirrors VALUES (?, ?, ?, ?, ?)",
                (
                    mirror.coin_id,
                    mirror.launcher_id,
                    mirror.amount.stream_to_bytes(),
                    b"".join(
                        [uint16(len(url)).stream_to_bytes() + url for url in Mirror.encode_urls(mirror.urls)]
                    ),  # prefix each item with a length
                    1 if mirror.ours else 0,
                ),
            )
```
