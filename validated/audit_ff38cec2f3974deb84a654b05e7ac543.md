### Title
Unbounded length-prefix decode in `is_atom_canonical` can raise an unhandled `IndexError` during mempool spend-bundle admission - (File: `chia/full_node/mempool_manager.py`)

### Summary
CVE-2018-11102 is an out-of-bounds read in Libav's `mov_probe` caused by decoding an attacker-controlled length/header field and then dereferencing the buffer at that computed offset without verifying enough bytes remain. Chia's `chia/full_node/mempool_manager.py` contains a structurally similar hand-rolled length-prefix decoder, `is_atom_canonical()` / `is_clvm_canonical()`, which walks a raw `bytes` buffer byte-by-byte using an `offset` that is advanced purely from header bits, with no bounds check against `len(clvm_buffer)` before each `clvm_buffer[offset]` access.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the header byte at `offset`, determines a `prefix_len` (0–5 extra bytes) from the top bits, and then loops to consume those bytes: [1](#0-0) 

Every access (`b = clvm_buffer[offset]`, and each `clvm_buffer[offset]` inside the `for i in range(prefix_len)` loop) is an unchecked index into the byte buffer. `is_clvm_canonical()` drives this decoder over the entire buffer, tracking `tokens_left` and `offset`, again without ever validating `offset < len(clvm_buffer)` before dereferencing: [2](#0-1) 

This is called directly on attacker-supplied bytes during spend-bundle admission — the `puzzle_reveal` and `solution` of every coin spend in a submitted `SpendBundle`: [3](#0-2) 

This mirrors the CVE-2018-11102 bug class exactly: a length/tag byte drives how many subsequent bytes are consumed, and the code advances an offset/pointer based on that value without first confirming the buffer is long enough, i.e. the same "decode header, then read past end of buffer" pattern found in `mov_probe`.

### Impact Explanation
If `offset` can be pushed to (or past) `len(clvm_buffer) - 1` while a token is still pending (e.g., a header byte suggesting a multi-byte length prefix sits at or near the very end of the buffer, or a nested-list token count causes the walk to continue after the last real atom), `clvm_buffer[offset]` raises an uncaught Python `IndexError`. Since this function is invoked synchronously inside `MempoolManager.validate_spend_bundle()`, an unhandled exception here would propagate out of the transaction-validation path rather than being converted into a normal `Err` rejection, which is a spend-triggered denial-of-service against the node's transaction-processing pipeline (the same crash-on-malformed-input impact class as the original CVE, "cause a denial of service (application crash)").

### Likelihood Explanation
I was not able to conclusively prove that a byte sequence reaching this function can actually be short enough to trigger the overrun, because by the time `is_clvm_canonical()` runs, the `puzzle_reveal`/`solution` bytes have already round-tripped through the Rust CLVM parser (`chia_rs.Program`) when the `CoinSpend`/`SpendBundle` was deserialized, and that parser is expected to reject truncated/malformed atom headers before this Python-side canonical check ever executes. I could not fully verify, within the available tool budget, whether the Rust parser's notion of "valid" input is byte-for-byte consistent with what this hand-rolled Python decoder assumes (in particular around back-reference `0xFE` interleavings or unusual nesting that the Rust parser permits but this duplicate implementation does not model identically). That gap is the main uncertainty in this analog, and it lowers confidence versus a proven, directly-reachable crash.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` and `is_clvm_canonical()` before every `clvm_buffer[offset]` access (raise/return "not canonical" instead of indexing past the buffer), and wrap the canonical-check call sites in `validate_spend_bundle()` in a defensive `try/except` that maps any parsing exception to `Err.INVALID_COIN_SOLUTION` rather than letting it propagate. This removes reliance on the assumption that upstream Rust parsing guarantees will always keep this duplicated Python decoder's offsets in bounds.

### Proof of Concept
Not confirmed. A concrete PoC would require constructing a `puzzle_reveal`/`solution` byte string that (a) is accepted by `chia_rs.Program`'s deserializer when building the `CoinSpend`/`SpendBundle`, and (b) causes `is_clvm_canonical()`'s `offset` to run past `len(clvm_buffer)` before `tokens_left` reaches 0. I could not verify such an input exists within the current investigation, so this should be treated as a code-pattern match (unchecked length-prefix decode identical to the CVE's bug class) rather than a proven exploit.

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
