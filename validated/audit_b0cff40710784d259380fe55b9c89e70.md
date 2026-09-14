### Title
Unbounded byte-index read in CLVM canonical-encoding check can crash mempool processing on a crafted spend bundle - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` walk an attacker-supplied CLVM byte buffer using a length prefix decoded from the buffer itself, advancing `offset` without ever checking that `offset` stays inside `len(clvm_buffer)`. This mirrors the libsoup `skip_insight_whitespace()` bug class described in the report: a scanning/parsing loop that trusts attacker-controlled length data to keep indexing a buffer and reads past its end. [1](#0-0) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the leading byte at `offset` and, depending on its top bits, derives a `prefix_len` of up to 5 additional bytes to consume for the atom's length prefix: [2](#0-1) 

```
b = clvm_buffer[offset]
...
elif (b & 0b11111110) == 0b11111100:
    prefix_len = 5
```

It then loops `prefix_len` times, incrementing `offset` and indexing `clvm_buffer[offset]` again, with no check that `offset < len(clvm_buffer)`: [3](#0-2) 

```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```

`is_clvm_canonical()` calls this in a loop driven entirely by attacker-controlled bytes (`b == 0xFF` for pairs, `b <= 0x80` for small atoms, otherwise `is_atom_canonical`) with no upper bound check on `offset` before dereferencing `clvm_buffer[offset]`: [4](#0-3) 

Because these operate on Python `bytes`, an out-of-range index does not produce a silent heap over-read as in the C `libsoup` case; it raises an uncaught `IndexError`. If this path is reached while validating/processing a submitted spend bundle's puzzle reveal or solution bytes (this module imports `ELIGIBLE_FOR_DEDUP`, `ELIGIBLE_FOR_FF`, and `eligible_coin_spends.can_fast_forward_singleton`, indicating these canonicality checks are used during mempool admission/fast-forward eligibility evaluation of spend data), a crafted last-byte length prefix (e.g., a `0xFC`–`0xFF` marker byte placed at or near the very end of the buffer) causes the loop to walk past the end of `clvm_buffer` and raise `IndexError`.

I was not able to fully confirm, within the available index, the exact call site that feeds attacker-controlled puzzle/solution bytes into `is_clvm_canonical()`/`is_atom_canonical()` (the direct callers are only visible in test files and a context doc, not the production call graph). This should be verified against the full source before treating this as confirmed-exploitable.

### Impact Explanation
If reachable from spend-bundle processing without a surrounding try/except, an unhandled `IndexError` during mempool item validation constitutes a spend-triggered halt of that validation path (potentially a full node exception/crash on ingestion of an untrusted transaction) — this falls under the accepted "spend-triggered transaction-processing halt" impact category. Severity would be Medium, consistent with the source advisory's Medium/6.5 rating, since it is a single out-of-bounds read triggered by network-reachable, single-submitter-controlled input, not memory corruption (Python bounds-checks the underlying buffer).

### Likelihood Explanation
Likelihood is uncertain without confirming the concrete caller/call path that supplies untrusted `clvm_buffer` bytes to `is_clvm_canonical()`. The function's placement in `mempool_manager.py` next to dedup/fast-forward eligibility logic, and its explicit purpose of validating "CLVM serialization is all canonical" for attacker-supplied puzzle reveals/solutions, suggests it is meant to run on untrusted spend-bundle data, making a crafted buffer plausible for any unprivileged bundle submitter to construct.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` dereference (both the initial read and each iteration of the prefix-length loop), raising a `ValueError`/returning "not canonical" instead of allowing an `IndexError` to propagate; add a similar bound check in `is_clvm_canonical()`'s main loop before indexing `clvm_buffer[offset]`. Ensure any caller path that invokes these functions on network-supplied spend bundle data wraps them (or the bounds check itself) so malformed input is rejected as invalid rather than raising an unhandled exception.

### Proof of Concept
Construct a CLVM buffer whose last byte begins a long atom-length prefix but omits the trailing prefix bytes, e.g.:
```
buf = b"\xfc\xff"   # 0xFC signals prefix_len = 5 in is_atom_canonical
```
Calling `is_atom_canonical(buf, 0)` executes `b = buf[0]` (0xFC → `prefix_len = 5`), then the loop attempts `offset` values 1..5, each doing `clvm_buffer[offset]`; since `len(buf) == 2`, the second iteration (`offset = 2`) raises `IndexError: index out of range`. If this buffer originates from an attacker-controlled puzzle reveal/solution passed through `is_clvm_canonical()` during spend bundle admission, the same unhandled exception occurs. [1](#0-0)

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
