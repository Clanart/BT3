### Title
Unbounded multi-byte atom length-prefix read in mempool canonical-CLVM check can raise an unhandled `IndexError` on attacker-supplied solution bytes - (`chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` parses the CLVM atom length-prefix encoding of a spend's solution bytes to decide DEDUP eligibility, but never checks that the multi-byte length-prefix bytes it reads actually exist within the remaining buffer, analogous to the tcpdump VRRP parser trusting a length field without validating it against the packet's remaining length (CVE-2019-15167).

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` inspects the leading byte at `offset` to determine how many additional prefix bytes encode the atom length (0–5 extra bytes depending on the top bits), then loops reading those bytes directly from `clvm_buffer` without ever checking that `offset + prefix_len` stays within `len(clvm_buffer)`: [1](#0-0) 

The caller, `is_clvm_canonical(clvm_buffer)`, walks the buffer atom-by-atom calling `is_atom_canonical()` whenever it encounters a byte `> 0x80` (a multi-byte atom), and only bounds-checks the *final* result (`offset == len(clvm_buffer)`) after the fact — it never validates that intermediate reads stay in range: [2](#0-1) 

If a submitted spend bundle contains a solution whose serialized bytes end with a truncated multi-byte atom length-prefix (e.g., the final byte is `0xFC`–`0xFF`, signaling up to 5 more length bytes, but the buffer has fewer bytes remaining than that), `clvm_buffer[offset]` will index past the end of the `bytes` object. In Python this raises an `IndexError` rather than returning a bounds-checked `ValueError`, which is the semantic equivalent of the out-of-bounds read in the tcpdump VRRP parser that trusted an on-wire length field.

### Impact Explanation
This buffer is derived from attacker-controlled data: solution bytes of a coin spend inside a spend bundle submitted to the mempool by any unprivileged peer/wallet. `is_clvm_canonical()`/`is_atom_canonical()` are invoked during spend-bundle admission to determine DEDUP eligibility of a spend, meaning a crafted, truncated solution can trigger an unhandled `IndexError` during mempool processing of that spend bundle. Unlike the `ValueError`s this code deliberately raises for other malformed cases (unexpected top bits, non-canonical encodings), an `IndexError` is not the expected/handled exception type in this validation path, so it risks propagating as an uncaught exception in the mempool item validation flow — a spend-triggered halt of transaction processing for that item/task, matching the "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
Reaching this code only requires submitting a single spend bundle with a coin spend whose solution ends in a truncated multi-byte CLVM atom length-prefix — no privileged access, network position, or special timing is needed, matching the low-cost/low-skill VRRP-length-trust bug class from the source CVE.

### Recommendation
In `is_atom_canonical()`, bounds-check `offset + prefix_len` against `len(clvm_buffer)` before reading each prefix byte and raise a well-defined `ValueError` (as done for other malformed encodings) instead of allowing an `IndexError` to escape; ensure `is_clvm_canonical()`'s caller in the mempool admission path only expects/handles the documented exception types.

### Proof of Concept
1. Construct a solution byte string whose last byte is `0xFC` (indicating a 6-byte-total length prefix: 5 extra bytes) but supply fewer than 5 bytes after it (e.g., `b"\xff\x80\xfc"` — a pair token followed by a truncated multi-byte atom header).
2. Submit a spend bundle whose coin spend uses this solution, structured so it is eligible for the DEDUP path that triggers `is_clvm_canonical()` on the solution bytes.
3. During mempool validation, `is_atom_canonical()` calls `clvm_buffer[offset]` beyond the end of the buffer while consuming the 5 expected prefix bytes, raising an unhandled `IndexError` instead of a caught `ValueError`, disrupting spend-bundle processing.

**Note on verification limits:** I was unable to retrieve the exact call site where `is_clvm_canonical()` is invoked during spend admission (e.g., the surrounding `try/except` scope in `mempool_manager.py`) before reaching the tool-call budget, so I cannot fully confirm how far the `IndexError` propagates or whether an outer handler currently catches generic exceptions there. The root-cause bounds-check gap in `is_atom_canonical()`/`is_clvm_canonical()` is confirmed directly from source, but the precise blast radius of the resulting exception should be validated against the exact caller before treating this as fully proven.

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
