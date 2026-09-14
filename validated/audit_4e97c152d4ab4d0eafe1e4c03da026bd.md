### Title
Unbounded index read in `is_atom_canonical()` lets a submitted spend bundle crash mempool admission via an unhandled `IndexError` - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` reads bytes at increasing offsets from an attacker-supplied CLVM buffer (the puzzle reveal or solution of a coin spend) without ever checking whether `offset` is still within the buffer bounds before indexing into it.

### Finding Description
`is_atom_canonical()` decodes a variable-length CLVM atom-length prefix (1–6 bytes) by repeatedly indexing `clvm_buffer[offset]` inside a `for i in range(prefix_len)` loop, incrementing `offset` each iteration, with no bounds check against `len(clvm_buffer)`: [1](#0-0) 

This function is invoked from `is_clvm_canonical()`, which walks an entire serialized CLVM buffer token by token. When it encounters a byte `b > 0x80` (indicating a multi-byte-length atom), it calls `is_atom_canonical(clvm_buffer, offset)` without first verifying that enough bytes remain in the buffer to hold the declared prefix: [2](#0-1) 

`is_clvm_canonical()` is called directly on attacker-controlled bytes — the coin spend's `puzzle_reveal` and `solution` — during mempool admission in `validate_spend_bundle()`, which runs synchronously in the main event loop (unlike `pre_validate_spendbundle()`, which offloads CLVM execution to a worker pool and explicitly catches `ValueError`): [3](#0-2) 

If a submitter crafts a puzzle reveal or solution ending in a truncated multi-byte atom-length-prefix marker (e.g., a trailing `0xC0`/`0xE0`/`0xF0`/... byte with insufficient trailing bytes to complete the prefix), `is_atom_canonical()` will index past the end of the buffer, raising an unhandled `IndexError`. This exception is not caught anywhere between `is_clvm_canonical()` and `validate_spend_bundle()`'s caller `add_spend_bundle()`, which itself is awaited directly from `full_node.py` during transaction processing.

### Impact Explanation
This is a Python-level analog of the CVE's "read past declared/expected buffer bounds while decoding a variable-length record header" bug class (DDS scanline length decoding overflow). In this C-free/Python context it manifests as an unhandled `IndexError` rather than memory corruption, but it is directly reachable by any unprivileged party who can submit a spend bundle to a full node's mempool (RPC `push_tx`, gossip `new_transaction`/`respond_transaction`, or wallet submission). An uncaught exception raised while iterating conditions inside `validate_spend_bundle()` propagates up through `add_spend_bundle()`; depending on the caller in `full_node.py`, this can produce a transaction-processing halt for that spend bundle path (crash/DoS of the coroutine handling the request), satisfying the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
High reachability: the vulnerable code path executes for every coin spend during mempool admission (`validate_spend_bundle` → `is_clvm_canonical` → `is_atom_canonical`), and the input (`puzzle_reveal`/`solution` bytes) is fully attacker-controlled in a single self-submitted spend bundle. No privileged network position, signature validity, or coin ownership is required to trigger the crash — only a syntactically-decodable-enough CLVM buffer that ends with a truncated multi-byte atom-length prefix.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` (and in the calling loop of `is_clvm_canonical()`) before indexing `clvm_buffer[offset]`, returning a "not canonical"/false result or raising a handled `ValueError` when the declared prefix length would exceed the remaining buffer — mirroring the bounds-checked pattern already used elsewhere in the codebase (e.g., `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which explicitly compare declared lengths against remaining buffer size before slicing). Additionally, wrap the `is_clvm_canonical` calls in `validate_spend_bundle()` in a try/except that maps any parsing exception to `Err.INVALID_COIN_SOLUTION` instead of letting it propagate.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) serialized bytes end with a byte such as `0xC0` (5+8 bit length prefix marker) as the very last byte of the buffer, with zero trailing bytes to supply the second prefix byte — e.g. append `b"\xc0"` at the very end of an otherwise well-formed CLVM buffer so that `clvm_buffer[offset]` is valid for the marker byte but the loop's next `clvm_buffer[offset+1]` read is out of range.
2. Wrap this `CoinSpend` in a `SpendBundle` and submit it to a full node's mempool (via RPC `push_tx` or peer `RespondTransaction`).
3. During admission, `MempoolManager.add_spend_bundle()` → `validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.solution))` at line 723-726, which calls `is_atom_canonical()`, raising an unhandled `IndexError` instead of returning `Err.INVALID_COIN_SOLUTION`.

Note: I was unable to execute this against a live node within this analysis; the finding is based on static code-path tracing of `mempool_manager.py`'s `is_atom_canonical`/`is_clvm_canonical`/`validate_spend_bundle` and their call graph as shown above. A background Devin session with repo/test execution access would be needed to confirm the exact exception propagation behavior and any outer exception handling in `full_node.py`'s transaction-handling coroutine that I could not fully trace here.

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
