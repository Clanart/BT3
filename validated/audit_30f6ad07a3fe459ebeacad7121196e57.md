### Title
Unbounded byte-buffer indexing in `is_atom_canonical()`/`is_clvm_canonical()` causes unhandled `IndexError` on crafted spend-bundle CLVM solutions - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a raw CLVM serialization buffer by manually walking length-prefix bytes with no bounds checking against the buffer's length, directly analogous to the unchecked byte-offset reads in `soup_headers_parse_request()` that caused CVE-2025-32906. A spend bundle submitted to the mempool with a truncated/crafted CLVM solution can drive these functions to index past the end of the buffer.

### Finding Description
`is_atom_canonical()` reads the buffer at `offset` and, depending on the atom's length-prefix form, loops up to 5 additional times reading `clvm_buffer[offset]` to build up `atom_len`, without ever checking that `offset` stays within `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` calls this in a loop, and itself performs `b = clvm_buffer[offset]` before each iteration without a bounds check, so a buffer that ends exactly where more tokens are still expected (`tokens_left != 0`) will also index out of range on the next loop pass: [2](#0-1) 

Per the module's own documentation, this canonical-form check exists specifically to gate whether a submitted spend's solution is eligible for DEDUP treatment during mempool admission of untrusted CLVM buffers coming from spend bundles: [3](#0-2) 

Because Python raises `IndexError: index out of range` on out-of-bounds `bytes` indexing rather than silently returning garbage, a crafted buffer (e.g., one whose final byte is a multi-byte length-prefix marker such as `0xFC` with insufficient trailing bytes, or one that ends mid-token while `tokens_left > 0`) will raise an uncaught `IndexError` inside this pure-Python parsing routine.

### Impact Explanation
If this exception is not caught by an enclosing handler in the mempool admission path, it can propagate out of spend-bundle validation and disrupt the transaction-processing worker/task handling that submission — a spend-triggered denial-of-service on mempool/transaction processing, matching the "spend-triggered transaction-processing halt" acceptance criterion. This mirrors the libsoup bug class: attacker-controlled, insufficiently-bounds-checked byte-offset parsing of a length-prefixed wire format leading to an out-of-bounds read that crashes the service handling the request.

### Likelihood Explanation
I was not able to fully trace, within this session, the exact call site that invokes `is_clvm_canonical()`/`is_atom_canonical()` during spend-bundle admission (only the function definitions and unit tests for them were confirmed via search); the documentation excerpt states they gate DEDUP eligibility, implying they run on submitter-controlled CLVM solution bytes during mempool admission, but I could not directly confirm that no outer `try/except` wraps this call in the production pre-validation flow, nor confirm whether an `IndexError` here is caught and converted into a normal validation-error response (in which case impact would be limited to a single rejected spend rather than a broader processing halt). This should be verified by inspecting the actual call site and its exception handling before treating this as confirmed-exploitable at High severity.

### Recommendation
Add explicit bounds checks before each buffer index access in `is_atom_canonical()` and `is_clvm_canonical()` (return `False`/treat as non-canonical rather than raising when `offset >= len(clvm_buffer)`), and ensure the mempool admission path wraps CLVM canonical-form checks in a handler that converts any parsing exception into a normal `Err`/rejection rather than letting it propagate.

### Proof of Concept
A spend bundle whose CoinSpend solution bytes are crafted so that, at some offset consumed during canonicality scanning, the buffer ends immediately after a multi-byte atom length-prefix marker (e.g., byte `0xFC`, which per `is_atom_canonical()` requires 5 more prefix bytes) — but the buffer is truncated at that point — causes `clvm_buffer[offset]` in the prefix-reading loop (`chia/full_node/mempool_manager.py:178-181`) to raise `IndexError` when invoked via `is_clvm_canonical()` (`chia/full_node/mempool_manager.py:186-227`) during mempool admission of that spend bundle.

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

**File:** .cursor/context/clvm-execution.md (L96-111)
```markdown
## Canonical serialization

**Location**: `mempool_manager.py:185`

### `is_clvm_canonical(clvm_buffer)`

Checks that a CLVM program uses shortest-form atom encoding:

- No unnecessary length prefix bytes
- No back-references (`0xFE` byte)
- No trailing garbage

### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.
```
