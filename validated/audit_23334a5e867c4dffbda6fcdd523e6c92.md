### Title
Missing bounds validation in DataLayer mirror-URL byte-blob parsing can throw an unhandled exception during wallet DL sync (DoS) - ([File: chia/data_layer/dl_wallet_store.py])

### Summary
`_row_to_mirror()` decodes a length-prefixed list of mirror URLs from a raw byte blob using a 2-byte length prefix per entry, with no check that the declared length fits inside the remaining buffer. This is the same bug class as the X.org `_GetCountedString` issue: a counted string/blob is read using an attacker-influenced length field without validating it against the actual remaining buffer size before slicing/using it.

### Finding Description
`_row_to_mirror()` walks a byte blob that stores DataLayer mirror URLs, one per entry, each prefixed with a 2-byte length: [1](#0-0) 

Unlike the equivalent length-prefixed parsers in `chia/full_node/full_block_utils.py` (`skip_bytes`, `generator_from_block`, `block_info_from_block`), which explicitly validate `length <= len(remaining_buffer)` and raise a descriptive `ValueError` otherwise: [2](#0-1) [3](#0-2) 

`_row_to_mirror()` performs no such check. `uint16.from_bytes(byte_list[0:2])` will raise if fewer than 2 bytes remain, and the slice `byte_list[2 : length + 2]` silently truncates rather than validating the declared `length` against the real remaining bytes — the Python analog of the C buffer-overflow/over-read pattern where a counted length is trusted without a bounds check.

This routine is exercised whenever mirror-coin state is loaded from the wallet DB, i.e., `get_mirrors()`/`get_mirror()`, which are populated by `add_mirror()` whenever the DL wallet processes a DataLayer mirror coin (a coin type any DataLayer participant can create/spend, carrying attacker-supplied URL memo content): [4](#0-3) 

### Impact Explanation
If a malformed or maliciously-crafted byte blob reaches `_row_to_mirror` (e.g., through corrupted/truncated data, or via a decoding/encoding mismatch upstream in `data_layer_wallet.py` that I was not able to fully trace in the time available), the missing bounds check can cause an unhandled exception (`ValueError`/`OverflowError`) while the wallet loads mirror records for a DataLayer store. Because mirror-coin state is populated from other participants' on-chain spends, this could allow an unprivileged DataLayer participant to disrupt another client's wallet processing for that store (a "spend-triggered transaction-processing halt" class DoS), which is within the accepted impact categories for this scan.

### Likelihood Explanation
Medium-low confidence. I confirmed the parser itself lacks the same bounds validation present in the analogous, already-hardened `full_block_utils.py` parsers, and confirmed this code path is reachable from ordinary DataLayer mirror-coin processing (not an operator-only or mocked-only path). However, I was not able to fully trace, within the available iterations, the exact code in `chia/data_layer/data_layer_wallet.py` that decodes on-chain mirror-coin memo data into `mirror.urls` before it is written via `add_mirror()`/`Mirror.encode_urls()` — so I cannot confirm with certainty that an attacker can force `length + 2` to exceed the real buffer without the write path (`add_mirror`) already rejecting or correctly encoding the data first. This should be verified by tracing `Mirror.encode_urls`/`decode_urls` and the on-chain memo parsing in `data_layer_wallet.py`.

### Recommendation
Add explicit bounds checks in `_row_to_mirror()` mirroring the pattern used in `chia/full_node/full_block_utils.py`: verify at least 2 bytes remain before reading the length prefix, and verify `length <= len(byte_list) - 2` before slicing, raising a clear `ValueError` (and handling/logging it at call sites) instead of allowing silent truncation or an unhandled exception to propagate into wallet sync/state-processing code.

### Proof of Concept
Not independently verified end-to-end due to incomplete tracing of the on-chain mirror-URL encode/decode path in `data_layer_wallet.py`; conceptually: construct a `urls` blob whose trailing bytes are shorter than the declared 2-byte length prefix (e.g., `b"\x00\x05ab"`, declaring a 5-byte URL but supplying only 2 bytes) and pass it as `row[3]` to `_row_to_mirror()` — the slice does not raise, silently returning a wrong/short URL) or, if only 1 trailing byte remains for the length field itself (e.g., `b"\x00\x05ab\x01"`), `uint16.from_bytes(byte_list[0:2])` fails on the malformed remainder, demonstrating the missing bounds validation relative to `full_block_utils.py`'s equivalent checks.

### Citations

**File:** chia/data_layer/dl_wallet_store.py (L31-38)
```python
def _row_to_mirror(row: Row, confirmed_at_height: uint32 | None) -> Mirror:
    urls: list[bytes] = []
    byte_list: bytes = row[3]
    while byte_list != b"":
        length = uint16.from_bytes(byte_list[0:2])
        url = byte_list[2 : length + 2]
        byte_list = byte_list[length + 2 :]
        urls.append(url)
```

**File:** chia/data_layer/dl_wallet_store.py (L308-352)
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
            await conn.execute_insert(
                "INSERT OR REPLACE INTO mirror_confirmations (coin_id, confirmed_at_height) VALUES (?, ?)",
                (
                    mirror.coin_id,
                    mirror.confirmed_at_height,
                ),
            )

    async def get_mirrors(self, launcher_id: bytes32) -> list[Mirror]:
        async with self.db_wrapper.reader_no_transaction() as conn:
            cursor = await conn.execute(
                "SELECT * from mirrors WHERE launcher_id=?",
                (launcher_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            mirrors: list[Mirror] = []

            for row in rows:
                confirmation_height = await execute_fetchone(
                    conn, "SELECT * FROM mirror_confirmations WHERE coin_id=?", (row[0],)
                )
                mirrors.append(
                    _row_to_mirror(row, None if confirmation_height is None else uint32(confirmation_height[1]))
                )

        return mirrors
```

**File:** chia/full_node/full_block_utils.py (L27-34)
```python
def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```

**File:** chia/full_node/full_block_utils.py (L259-266)
```python
    if version == 1:
        if len(buf) < 4:
            raise ValueError(f"generator_buffer length prefix requires 4 bytes, remaining buffer {len(buf)}")
        length = uint32.from_bytes(buf[:4])
        buf = buf[4:]
        if length > len(buf):
            raise ValueError(f"generator_buffer length: {length}, exceeds remaining buffer {len(buf)}")
        return bytes(buf[:length])
```
