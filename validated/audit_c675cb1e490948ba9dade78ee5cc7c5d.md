### Title
Out-of-bounds byte index in `is_atom_canonical`/`is_clvm_canonical` from unchecked length-prefix parsing on attacker-controlled spend data - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` decodes a CLVM atom's variable-length size prefix (1–6 bytes) directly out of a raw `bytes` buffer supplied by an external spend bundle, and its caller `is_clvm_canonical()` walks the whole puzzle/solution buffer using the prefix length it just decoded, without ever confirming that `offset` (or the prefix bytes it is about to read) stays inside `len(clvm_buffer)`. This mirrors the UPX `canUnpack` bug class in CVE-2019-20053: a header/size field taken from crafted, untrusted input is used to index into a buffer without a bounds check, producing an invalid/out-of-range memory access when the file (here: the spend's puzzle reveal or solution bytes) is truncated relative to what its own length prefix claims.

### Finding Description
`is_atom_canonical()` reads the length-prefix byte at `clvm_buffer[offset]`, decides a `prefix_len` (0–5 extra bytes) from the top bits, and then loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]` with no check that the buffer actually has that many bytes remaining: [1](#0-0) 

`is_clvm_canonical()` drives this by repeatedly indexing `clvm_buffer[offset]` and advancing `offset` by whatever `atom_len` (fully attacker-controlled) was decoded, again without validating that the new `offset` is within bounds before the next `clvm_buffer[offset]` read on the following loop iteration: [2](#0-1) 

Per the module's own documentation, this canonical-form check runs over the puzzle/solution bytes of coin spends inside a submitted `SpendBundle` when determining DEDUP eligibility during mempool admission: [3](#0-2) 

A submitter can craft a puzzle reveal or solution whose final atom's length-prefix byte claims a multi-byte prefix (e.g. the `0xFE`-style 5-extra-byte prefix pattern) but truncate the actual buffer right after that byte. Because there is no length check before the `clvm_buffer[offset]` dereference, this raises an unguarded `IndexError` deep inside spend-bundle admission logic rather than being handled as a normal validation rejection.

### Impact Explanation
This function sits directly on the mempool admission path for untrusted, attacker-supplied CLVM byte buffers (puzzle reveal / solution of any coin spend in a submitted spend bundle). An unhandled `IndexError` raised from this bounds-unchecked parser, if not caught by an enclosing generic exception handler, can abort processing of that spend bundle's admission/eligibility computation unexpectedly, which is a spend-triggered fault in transaction-processing logic — the class of impact explicitly accepted by the validation rules (spend-triggered transaction-processing halt). It is reachable purely from a single, unprivileged, submitted spend bundle with no network/peer trust required.

### Likelihood Explanation
Likelihood is high for triggering the code path itself, since any wallet/user/CLI/RPC caller can submit a spend bundle with an arbitrary puzzle reveal/solution byte string, and DEDUP-eligibility checking runs canonical-form validation on that data automatically during normal mempool admission. What is not confirmed from the available code is whether the exact call site wraps `is_clvm_canonical()` in a broad `try/except` that would swallow the `IndexError` and convert it into a normal `Err` rejection instead of crashing a worker — that call site was not fully located within the available context, so the ultimate blast radius (isolated rejection vs. propagating fault) is uncertain and should be verified directly against `chia/full_node/mempool_manager.py` and its callers (e.g., eligible-for-dedup determination in `eligible_coin_spends.py`/`add_spend_bundle`).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` dereference (verify `offset < len(clvm_buffer)` prior to reading the prefix byte and each subsequent prefix-continuation byte), and in `is_clvm_canonical()` before indexing `clvm_buffer[offset]` on every loop iteration. On out-of-range access, return `False` (non-canonical) or raise a well-defined `ValidationError`/`Err` rather than allowing a raw `IndexError` to propagate, and ensure the caller in the mempool admission path always translates any such error into a normal spend-bundle rejection.

### Proof of Concept
1. Construct a coin spend whose puzzle reveal (or solution) bytes end with a length-prefix byte matching one of the multi-byte-prefix patterns in `is_atom_canonical` (e.g., byte `0xF8` indicating a 4-extra-byte prefix per `chia/full_node/mempool_manager.py:161-170`), but truncate the buffer so fewer than 4 bytes follow.
2. Submit a `SpendBundle` containing this coin spend for mempool admission (e.g., via `push_tx`/RPC), triggering the DEDUP-eligibility canonical-form check on this puzzle/solution buffer as described in `.cursor/context/clvm-execution.md:96-117`.
3. `is_atom_canonical()` reads past the end of `clvm_buffer` in its `for i in range(prefix_len): ... atom_len |= clvm_buffer[offset]` loop, raising `IndexError` (`chia/full_node/mempool_manager.py:178-181`), which propagates out of `is_clvm_canonical()` unless caught by a generic handler somewhere in the admission call chain.

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

**File:** .cursor/context/clvm-execution.md (L96-117)
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

### `is_atom_canonical(clvm_buffer, offset)`

Validates a single atom's length prefix encoding. The CLVM format uses
variable-length prefixes (1-6 bytes) based on atom size. Each prefix
length has a minimum atom size threshold.
```
