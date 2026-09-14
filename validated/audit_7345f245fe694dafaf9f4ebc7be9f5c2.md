### Title
Unbounded CLVM atom length-prefix parsing causes out-of-bounds buffer read in mempool canonical-encoding check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_clvm_canonical()` and `is_atom_canonical()` in `chia/full_node/mempool_manager.py` parse a CLVM atom's length-prefix (1–6 bytes, similarly to the AVP length parsing in the accel-ppp CVE) and then index into the raw `bytes` buffer using the *attacker-supplied* length value without ever validating that value against the actual remaining size of the buffer. This is invoked directly on the untrusted `puzzle_reveal` and `solution` bytes of every coin spend inside `MempoolManager.validate_spend_bundle()`, a path any unprivileged spend-bundle submitter reaches on every submission.

### Finding Description
`is_atom_canonical()` decodes a length-prefix byte and then reads `prefix_len` (up to 5) additional bytes from `clvm_buffer` by indexing `clvm_buffer[offset]` in a loop, with no check that `offset` stays inside the buffer: [1](#0-0) 

It then returns `1 + prefix_len + atom_len`, where `atom_len` is fully attacker-controlled (decoded straight from the buffer). The caller, `is_clvm_canonical()`, advances its own cursor by this untrusted `atom_len` and immediately re-indexes the buffer at the new offset on the next loop iteration, with no bounds check that `offset + atom_len <= len(clvm_buffer)`: [2](#0-1) 

This is structurally the same bug class as CVE-2020-15173: a length field taken from untrusted input is used to index/advance into a buffer without first validating it against the buffer's actual remaining size, exactly as accel-ppp trusted an L2TP AVP length field without checking it against the packet size before copying/reading. In Python, the out-of-bounds `bytes` access raises `IndexError` rather than causing memory corruption, but the missing-bounds-check root cause is identical.

`is_clvm_canonical()` is invoked directly on attacker-controlled bytes for every coin spend during mempool admission: [3](#0-2) 

This call happens inside `MempoolManager.validate_spend_bundle()`, the core per-spend-bundle admission routine reachable from any unprivileged spend-bundle submission (`add_spend_bundle` → `validate_spend_bundle`): [4](#0-3) 

Contrast this with the sibling parsers in `chia/full_node/full_block_utils.py`, which handle the same class of length-prefixed untrusted data and explicitly bounds-check every length against the remaining buffer before indexing (e.g. `skip_bytes`, `skip_list`, `generator_from_block`), raising a controlled `ValueError` instead of letting an out-of-range index reach the underlying container: [5](#0-4) [6](#0-5) 

`is_atom_canonical`/`is_clvm_canonical` lack this defensive pattern entirely.

### Impact Explanation
A malicious CLVM `puzzle_reveal` or `solution` byte string with a length-prefixed atom whose encoded length exceeds the bytes actually present (and where the surrounding structure keeps `tokens_left != 0` after the malformed atom, forcing another loop iteration) causes `is_clvm_canonical()` to index past the end of the buffer and raise an unhandled `IndexError` instead of the intended `False`/`ValidationError`. Since this executes inside the per-coin-spend loop of `validate_spend_bundle()`, an unprivileged caller can submit a single malformed spend bundle to trigger this path on any full node processing that mempool admission, resulting in an unhandled exception during transaction processing (a spend-triggered processing halt for that request), rather than the intended clean `Err.INVALID_COIN_SOLUTION` rejection.

### Likelihood Explanation
High: the code path is reached unconditionally for every coin spend in every submitted spend bundle (`validate_spend_bundle()` calls `is_clvm_canonical()` on both `puzzle_reveal` and `solution` bytes of each spend), and constructing a CLVM byte string with an out-of-range multi-byte atom length prefix is trivial and requires no special privileges — only a spend bundle with a non-canonical/malformed puzzle reveal or solution.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each indexed read of `clvm_buffer[offset]` for the length-prefix bytes, and in `is_clvm_canonical()` verify `offset + atom_len <= len(clvm_buffer)` before advancing the cursor and re-indexing on the next loop iteration — mirroring the defensive length checks already used in `chia/full_node/full_block_utils.py` (`skip_bytes`, `skip_list`, `generator_from_block`), returning `False`/raising a handled error instead of allowing an out-of-range index.

### Proof of Concept
Construct a CLVM buffer representing a pair `(atom . NIL)` where `atom`'s length prefix claims more bytes than are actually present, forcing a second loop iteration that indexes past the buffer end:
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xff -> pair marker (tokens_left becomes 2)
# 0xc0 0x7f -> "6+8 bits length prefix" atom claiming length 0x7f (127) bytes
#              but no such bytes follow
# is_clvm_canonical will process the pair, then try to skip 127 bytes for the
# atom, landing far past the end of the 2-byte buffer, then attempt to read
# clvm_buffer[offset] again since tokens_left != 0, raising IndexError
# instead of returning False.
clvm_buf = bytes.fromhex("ffc07f")
is_clvm_canonical(clvm_buf)  # raises IndexError instead of returning False
```
This mirrors `test_atom_not_canonical`/`test_clvm_not_canonical` cases already present in the test suite, which only test truncated-*prefix* buffers (`fc03ffffffff`, etc.) but do not test the case where the fully-decoded `atom_len` itself exceeds the remaining buffer length after a successfully decoded prefix: [7](#0-6)

### Citations

**File:** chia/full_node/mempool_manager.py (L144-183)
```python
def is_atom_canonical(clvm_buffer: bytes, offset: int) -> tuple[int, bool]:
    b = clvm_buffer[offset]
    if (b & 0b11000000) == 0b10000000:
        # 6 bits length prefix
        mask = 0b00111111
        prefix_len = 0
        min_value = 1
    elif (b & 0b11100000) == 0b11000000:
        # 5 + 8 bits length prefix
        mask = 0b00011111
        prefix_len = 1
        min_value = 1 << 6
    elif (b & 0b11110000) == 0b11100000:
        # 4 + 8 + 8 bits length prefix
        mask = 0b00001111
        prefix_len = 2
        min_value = 1 << (5 + 8)
    elif (b & 0b11111000) == 0b11110000:
        # 3 + 8 + 8 + 8 bits length prefix
        mask = 0b00000111
        prefix_len = 3
        min_value = 1 << (4 + 8 + 8)
    elif (b & 0b11111100) == 0b11111000:
        # 2 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000011
        prefix_len = 4
        min_value = 1 << (3 + 8 + 8 + 8)
    elif (b & 0b11111110) == 0b11111100:
        # 1 + 8 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000001
        prefix_len = 5
        min_value = 1 << (2 + 8 + 8 + 8 + 8)

    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value
```

**File:** chia/full_node/mempool_manager.py (L186-227)
```python
def is_clvm_canonical(clvm_buffer: bytes) -> bool:
    """
    checks whether the CLVM serialization is all canonical representation.
    atoms can be serialized in more than one way by using more bytes than
    necessary to encode the length prefix. This functions ensures that all atoms are
    encoded with the shortest representation. back-references are not allowed
    and will make this function return false
    """
    assert clvm_buffer != b""

    offset = 0
    tokens_left = 1
    while True:
        b = clvm_buffer[offset]

        # pair
        if b == 0xFF:
            tokens_left += 1
            offset += 1
            continue

        # back references cannot be considered canonical, since they may be
        # encoded in many different ways
        if b == 0xFE:
            return False

        # small atom or NIL
        if b <= 0x80:
            tokens_left -= 1
            offset += 1
        else:
            atom_len, canonical = is_atom_canonical(clvm_buffer, offset)
            if not canonical:
                return False
            tokens_left -= 1
            offset += atom_len

        if tokens_left == 0:
            break

    # if there's garbage at the end, it's not canonical
    return offset == len(clvm_buffer)
```

**File:** chia/full_node/mempool_manager.py (L670-726)
```python
    async def validate_spend_bundle(
        self,
        new_spend: SpendBundle,
        conds: SpendBundleConditions,
        spend_name: bytes32,
        first_added_height: uint32,
        get_coin_records: Callable[[Collection[bytes32]], Awaitable[list[CoinRecord]]],
        get_unspent_lineage_info_for_puzzle_hash: Callable[[bytes32], Awaitable[UnspentLineageInfo | None]],
    ) -> tuple[Err | None, MempoolItem | None, list[bytes32]]:
        """
        Validates new_spend with the given SpendBundleConditions, and
        spend_name, and the current mempool. The mempool should
        be locked during this call (blockchain lock).

        Args:
            new_spend: spend bundle to validate
            conds: result of running the clvm transaction
            spend_name: hash of the spend bundle data, passed in as an optimization
            first_added_height: The block height that `new_spend`  first entered this node's mempool.
                Used to estimate how long a spend has taken to be included on the chain.
                This value could differ node to node. Not preserved across full_node restarts.

        Returns:
            Optional[Err]: Err is set if we cannot add to the mempool, None if we will immediately add to mempool
            Optional[MempoolItem]: the item to add (to mempool or pending pool)
            list[bytes32]: conflicting mempool items to remove, if no Err
        """
        start_time = time.monotonic()
        if self.peak is None:
            return Err.MEMPOOL_NOT_INITIALIZED, None, []

        cost = conds.cost

        removal_names: set[bytes32] = set()
        additions_dict: dict[bytes32, Coin] = {}
        addition_amount: int = 0

        # Map of coin ID to SpendConditions
        spend_conditions = {bytes32(spend.coin_id): spend for spend in conds.spends}

        # if this happens, the SpendBundle doesn't match the
        # SpendBundleConditions.
        assert len(new_spend.coin_spends) == len(spend_conditions)

        bundle_coin_spends: dict[bytes32, BundleCoinSpend] = {}
        for coin_spend in new_spend.coin_spends:
            coin_id = coin_spend.coin.name()
            removal_names.add(coin_id)

            # if this coin_id isn't found, the SpendBundle doesn't match the
            # SpendBundleConditions.
            spend_conds = spend_conditions.pop(coin_id)

            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```

**File:** chia/full_node/full_block_utils.py (L15-34)
```python
def skip_list(buf: memoryview, skip_item: Callable[[memoryview], memoryview]) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"list count prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"list count {n} exceeds remaining buffer {len(buf)}")
    for _ in range(n):
        buf = skip_item(buf)
    return buf


def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```

**File:** chia/full_node/full_block_utils.py (L259-267)
```python
    if version == 1:
        if len(buf) < 4:
            raise ValueError(f"generator_buffer length prefix requires 4 bytes, remaining buffer {len(buf)}")
        length = uint32.from_bytes(buf[:4])
        buf = buf[4:]
        if length > len(buf):
            raise ValueError(f"generator_buffer length: {length}, exceeds remaining buffer {len(buf)}")
        return bytes(buf[:length])
    raise ValueError(f"invalid FullBlock generator version: {version}")
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L130-178)
```python
@pytest.mark.parametrize("clvm_hex", ["80", "ff8080", "ff7f03", "ffff8080ff8080"])
def test_clvm_canonical(clvm_hex: str) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    assert is_clvm_canonical(clvm_buf)


@pytest.mark.parametrize(
    "clvm_hex",
    [
        "fffe80",
        "c000",
        "c03f",
        "e00000",
        "e01fff",
        "f0000000",
        "f00fffff",
        "f800000000",
        "f807ffffff",
        "fc0000000000",
        "fc03ffffffff",
        "fe",
        "ff808080",
    ],
)
def test_clvm_not_canonical(clvm_hex: str) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    assert not is_clvm_canonical(clvm_buf)


@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c000", 2 + 0),
        ("c03f", 2 + 0x3F),
        ("e00000", 3 + 0),
        ("e01fff", 3 + 0x1FFF),
        ("f0000000", 4 + 0),
        ("f00fffff", 4 + 0xFFFFF),
        ("f800000000", 5 + 0),
        ("f807ffffff", 5 + 0x7FFFFFF),
        ("fc0000000000", 6 + 0),
        ("fc03ffffffff", 6 + 0x3FFFFFFFF),
    ],
)
def test_atom_not_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert not is_canonical
```
