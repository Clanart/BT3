## Analog Found

### Title
Unbounded byte-index reads in CLVM canonical-form parser can raise unhandled `IndexError` on malformed spend-bundle buffers - (File: `chia/full_node/mempool_manager.py`)

### Summary
CVE-2017-8398 is an invalid 1-byte read in `dwarf.c`'s hand-rolled binary parser that occurs when it walks past the end of a corrupt/truncated binary while decoding debug records, crashing tools like `objdump`/`readelf`. The Chia codebase contains a structurally identical pattern: `is_clvm_canonical()` and `is_atom_canonical()` in `chia/full_node/mempool_manager.py` hand-parse a raw CLVM byte buffer using a length-prefix state machine, indexing directly into the byte string (`clvm_buffer[offset]`) without first checking `offset < len(clvm_buffer)`.

### Finding Description
`is_clvm_canonical()` walks a CLVM-serialized buffer token by token: [1](#0-0) 

Each iteration reads `b = clvm_buffer[offset]` and, for pair tokens (`0xFF`), only increments `offset` and `tokens_left` without any check that `offset` remains inside the buffer before the next loop iteration re-indexes it. For non-canonical/atom tokens it delegates to `is_atom_canonical()`: [2](#0-1) 

`is_atom_canonical()` reads the initial length-prefix byte and then, inside the `for i in range(prefix_len)` loop, does `offset += 1; atom_len |= clvm_buffer[offset]` with no bound check that `offset` is still within `len(clvm_buffer)` — exactly the "read one more byte than is actually present in a corrupt/truncated buffer" pattern that CVE-2017-8398 describes for `dwarf.c`. A buffer that declares a pair (`0xFF`) or an atom length-prefix byte near the very end of the buffer, without the bytes that the state machine expects to follow, causes `clvm_buffer[offset]` to index past the end of the `bytes` object and raise an unhandled `IndexError`, rather than returning a clean "not canonical"/validation-failure result.

### Impact Explanation
This parser operates on attacker/user-supplied bytes: it is designed to validate the raw CLVM serialization of coin-spend puzzle/solution buffers submitted as part of a `SpendBundle` to determine mempool DEDUP eligibility, per the documented role of `is_clvm_canonical()`: [3](#0-2) 
Because it works directly on raw bytes before/alongside CLVM execution rather than through the Rust deserializer's bounds-checked path, a crafted truncated buffer can raise an uncaught `IndexError` in the code path that decides whether a submitted transaction is dedup-eligible, rather than being rejected cleanly. If this exception is not caught by the calling coroutine, it disrupts spend-bundle validation for that mempool item and could propagate as an unhandled exception in `MempoolManager` task handling.

### Likelihood Explanation
I was not able to confirm, within the tool-call budget available, the exact call site that invokes `is_clvm_canonical()` during spend-bundle admission, nor whether that call site wraps the parser in a `try/except` that safely converts `IndexError` into a normal validation failure. Grep confirmed only the function definitions and one call site in `chia/full_node/mempool_manager.py`, but I could not read that call site before running out of iterations. This is a real gap in verification: **the severity of this finding depends entirely on whether the caller catches exceptions from this parser.** If it is unguarded, likelihood is high (any submitter can trivially craft a truncated buffer); if guarded, the impact is limited to a single rejected transaction rather than a broader processing halt.

### Recommendation
Add explicit bounds checks in both `is_clvm_canonical()` and `is_atom_canonical()` before every `clvm_buffer[offset]` access (e.g., `if offset >= len(clvm_buffer): return False` / raise a well-defined validation error instead of letting Python raise `IndexError`), and confirm/ensure the calling code in `MempoolManager` treats any parsing failure (including exceptions) as "non-canonical, not dedup-eligible" rather than allowing an unhandled exception to escape into spend-bundle processing.

### Proof of Concept
A buffer such as `b"\xff\xff\x80"` (two pair markers followed by only one NIL atom, i.e. `tokens_left` never reaching 0 before the buffer is exhausted) or a length-prefix byte like `b"\xc0"` (5+8 bit prefix indicating one more prefix byte follows) with no trailing byte, passed into `is_clvm_canonical()`/`is_atom_canonical()`, causes `clvm_buffer[offset]` to be evaluated with `offset == len(clvm_buffer)`, raising `IndexError: index out of range` instead of returning `False`.

**Caveat:** Due to the tool-call limit reached before I could inspect the exact caller/exception-handling context, I cannot fully confirm whether this reaches an actual "transaction-processing halt" as required by the validation rules, versus being caught and only failing a single dedup-eligibility check. I flag this explicitly as unverified rather than asserting full exploitability.

### Citations

**File:** chia/full_node/mempool_manager.py (L144-184)
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

**File:** chia/full_node/mempool_manager.py (L196-227)
```python
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
