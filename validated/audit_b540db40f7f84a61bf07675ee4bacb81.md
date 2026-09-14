Confirmed: `is_clvm_canonical()` at `chia/full_node/mempool_manager.py:723-726` is called unconditionally on every coin spend's `puzzle_reveal` and `solution` bytes in `MempoolManager.validate_spend_bundle()`, directly on data from a spend bundle submitted by an unprivileged user, before the mempool decides admission.

### Title
Out-of-bounds read / unhandled `IndexError` in CLVM canonical-encoding check on attacker-controlled spend bundle data - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` reads length-prefix continuation bytes from a caller-supplied `bytes` buffer without validating that the buffer is long enough to contain the full multi-byte length prefix implied by the leading byte. `is_clvm_canonical()`, which calls it, is invoked from `MempoolManager.validate_spend_bundle()` on the raw `puzzle_reveal` and `solution` bytes of every coin spend in a submitted `SpendBundle`, i.e. fully attacker-controlled bytes reachable by any unprivileged spend-bundle submitter.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` at [1](#0-0)  decodes a variable-length atom-length prefix (0-5 continuation bytes depending on the high bits of the first byte) by looping `prefix_len` times and indexing `clvm_buffer[offset]` after incrementing `offset` each iteration, with no bounds check against `len(clvm_buffer)`:
```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```
`is_clvm_canonical()` at [2](#0-1)  walks the buffer token-by-token and calls `is_atom_canonical` whenever it sees a byte `> 0x80` that isn't `0xFF`/`0xFE`, again without checking that enough trailing bytes remain in the buffer for the indicated prefix length before dereferencing them.

This function is reached directly from `MempoolManager.validate_spend_bundle()`:
```
if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
    bytes(coin_spend.solution)
):
    return Err.INVALID_COIN_SOLUTION, None, []
``` [3](#0-2) 

Because `coin_spend.puzzle_reveal` and `coin_spend.solution` are taken verbatim from the submitted `SpendBundle`, a bundle whose last atom encodes a large multi-byte length prefix but truncates the buffer right after the leading prefix byte (e.g. hex `f0` followed by fewer than the 3 required continuation bytes) causes `clvm_buffer[offset]` to index past the end of the `bytes` object. In CPython, indexing a `bytes` object out of range raises `IndexError` rather than returning corrupted memory (unlike the C-level `skb_headlen` OOB read in the referenced CVE), so this is a memory-safety-adjacent parsing bug rather than exploitable memory corruption in this Python codebase.

### Impact Explanation
`validate_spend_bundle()` does not wrap the `is_clvm_canonical` calls in a `try/except`, so an `IndexError` propagates up out of the coroutine. Depending on the caller context (thread-pool task vs. direct await in the mempool-manager's spend-bundle admission path), this can surface as an unhandled exception during spend-bundle processing, which is a spend-triggered disruption of mempool transaction processing for a single submitted bundle. It does not enable unsigned/unauthorized coin movement, supply inflation, or coin-set divergence — it is, at most, a crash/DoS-class defect from malformed bundle bytes reaching an unchecked buffer walk.

### Likelihood Explanation
High likelihood of reachability: every coin spend in every submitted spend bundle passes through this exact code path in `validate_spend_bundle()` before other checks such as signature or cost validation reject it, so a minimal, cheaply constructed truncated puzzle_reveal/solution buffer is sufficient to trigger the code path. Whether it actually raises depends on exact byte-length edge cases (a truncated 1-byte prefix atom at the very end of the buffer with `prefix_len > 0` and insufficient trailing bytes).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (compare `offset` against `len(clvm_buffer)` and return "not canonical" or raise a handled `ConsensusError`/`ValueError` instead of letting `IndexError` escape), and ensure `MempoolManager.validate_spend_bundle()` treats any parsing failure from `is_clvm_canonical` as `Err.INVALID_COIN_SOLUTION` rather than propagating an unhandled exception.

### Proof of Concept
Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) is a truncated buffer such as bytes `f0` (indicating a 3-continuation-byte, i.e. `0xE0`-class length prefix requiring 3 more bytes) with zero or one trailing bytes, e.g. `bytes.fromhex("f0")` or `bytes.fromhex("f000")`. Submitting a `SpendBundle` containing a coin spend with this as `solution` and passing it through `MempoolManager.validate_spend_bundle()` reaches `is_clvm_canonical(bytes(coin_spend.solution))` → `is_atom_canonical(clvm_buffer, offset)`, which attempts to read `clvm_buffer[offset]` beyond the buffer length and raises `IndexError`. This can be verified by calling `is_atom_canonical` directly with such inputs, mirroring the boundary cases already exercised (but not for truncation) in `chia/_tests/core/mempool/test_mempool_manager.py:130-195` [4](#0-3) , none of which test a buffer truncated mid-prefix.

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

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L159-196)
```python
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


@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c040", 2 + 0x40),
        ("e02000", 3 + 0x2000),
        ("f0100000", 4 + 0x100000),
        ("f808000000", 5 + 0x8000000),
        ("fc0400000000", 6 + 0x400000000),
    ],
)
def test_atom_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert is_canonical

```
