### Title
Unbounded buffer read in `is_atom_canonical`/`is_clvm_canonical` on attacker-controlled solution bytes causes unhandled `IndexError` during DEDUP-eligibility checks - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` walk a raw CLVM byte buffer using an `offset` cursor that is advanced based on length-prefix bits decoded from the buffer itself, without ever validating that `offset` (or `offset + prefix_len`) stays within `len(clvm_buffer)` before indexing into it. This mirrors the root cause of CVE-2017-6347: code makes an implicit assumption about the layout/size of an attacker/locally-supplied buffer and indexes past its bounds when that assumption is violated by crafted input.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and then, depending on the high bits of `b`, sets `prefix_len` up to 5 and loops:

```python
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
``` [1](#0-0) 

There is no check that `offset` (before or after incrementing) stays `< len(clvm_buffer)`. The caller `is_clvm_canonical()` similarly advances `offset` by `atom_len` (a value fully controlled by the attacker via the encoded length prefix) and then, on the next loop iteration, re-indexes `clvm_buffer[offset]` without checking that the new offset is in range:

```python
else:
    atom_len, canonical = is_atom_canonical(clvm_buffer, offset)
    if not canonical:
        return False
    tokens_left -= 1
    offset += atom_len

if tokens_left == 0:
    break
``` [2](#0-1) 

Per `.cursor/context/clvm-execution.md`, `is_clvm_canonical()` is used to gate DEDUP-eligible spends and is documented as being invoked from `mempool_manager.py` when processing a submitted spend bundle's solution bytes to determine whether identical solutions can be deduplicated in the mempool [3](#0-2) . The solution bytes originate from an attacker-controlled `SpendBundle` reachable by any unprivileged mempool submitter — this is the same trust boundary (locally supplied, self-crafted byte stream misinterpreted due to an unchecked layout assumption) that the kernel CVE exploited via `MSG_MORE`/loopback UDP crafted syscalls.

Because Python raises `IndexError` on out-of-range bytes/`bytes`-like indexing rather than silently reading adjacent memory, the practical manifestation is not memory disclosure but an unhandled exception. I was not able to confirm within this session whether the call site that invokes `is_clvm_canonical()` wraps it in a try/except; this is the load-bearing uncertainty in the finding.

### Impact Explanation
If the exception is not caught by the caller, a single crafted spend bundle can throw an unhandled `IndexError` during mempool item construction/eligibility evaluation, which — depending on where in the mempool add pipeline this runs — could disrupt processing of that spend bundle or, if it propagates through a shared task/executor without isolation, halt broader mempool/transaction processing. This falls under the accepted "spend-triggered transaction-processing halt" impact category. It does not, by itself, permit unauthorized coin movement, supply inflation, or consensus divergence.

### Likelihood Explanation
Likelihood is high for triggering the bug (constructing an atom whose length-prefix implies more bytes than remain in the buffer, or that pushes `offset` past the end for the next loop iteration, is trivial and fully within submitter control), but the actual availability/severity depends on unverified exception-handling behavior at the call site, which limits confidence to "Medium" without further code access.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each buffer access (`offset < len(clvm_buffer)` and `offset + prefix_len < len(clvm_buffer)`), and in `is_clvm_canonical()`'s main loop verify `offset < len(clvm_buffer)` before dereferencing `clvm_buffer[offset]` on every iteration, returning `False` (non-canonical) instead of raising when the buffer is truncated relative to the encoded length. Additionally, ensure any caller of `is_clvm_canonical()` treats malformed/truncated CLVM buffers as a well-defined validation failure rather than relying on unhandled exceptions.

### Proof of Concept
Construct a `Program`/solution byte buffer that starts with an atom-length prefix byte indicating the 5-extra-byte (`0xFC`) encoding but supply fewer than 5 trailing bytes, e.g. `bytes([0xFC])` (or `0xFC` followed by only 1–2 bytes) as the `clvm_buffer` passed into `is_clvm_canonical()`. The `for i in range(prefix_len)` loop in `is_atom_canonical()` will attempt `clvm_buffer[offset]` past the end of the buffer and raise `IndexError`, exactly analogous to feeding `ip_cmsg_recv_checksum()` an `skb` whose actual data layout doesn't match the code's length assumptions. Since this buffer originates from the `solution` bytes of an attacker-submitted `CoinSpend`/`SpendBundle`, exploitation requires nothing more than submitting a spend bundle whose solution program is not canonically serialized in this crafted way.

### Citations

**File:** chia/full_node/mempool_manager.py (L177-183)
```python
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
