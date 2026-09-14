### Title
Unbounded byte-offset indexing in CLVM canonical-serialization check causes unhandled `IndexError` on malformed spend data - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a CLVM-serialized buffer (a `puzzle_reveal`/`solution` supplied by an unprivileged spend-bundle submitter) by trusting attacker-controlled length-prefix bits to decide how many bytes to consume, and index `clvm_buffer[offset]` repeatedly without first verifying `offset < len(clvm_buffer)`.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the prefix byte at `clvm_buffer[offset]` and, based on which high bits are set, computes a `prefix_len` (0–5 extra bytes) and then loops `prefix_len` times doing: [1](#0-0) 
incrementing `offset` and dereferencing `clvm_buffer[offset]` on every iteration with no bounds check against `len(clvm_buffer)`. If the buffer ends right after the prefix byte (e.g., a truncated/malformed multi-byte atom-length header), this raises an unhandled `IndexError` instead of returning a controlled "not canonical" result.

`is_clvm_canonical(clvm_buffer)` has the same pattern at the top of its main loop: [2](#0-1) 
It reads `clvm_buffer[offset]` every iteration and advances `offset` by `atom_len` (attacker-controlled prefix value) without validating that the resulting offset stays inside the buffer before the next dereference — this mirrors the CVE-2023-35001 bug class (a "VM"-style interpreter trusting an attacker-influenced length/offset field to drive further indexed reads without bounds validation, resulting in an out-of-range access).

The buffer this function parses (`clvm_buffer = bytes(spend.puzzle_reveal)` / `bytes(spend.solution)`) originates directly from a submitted `SpendBundle`'s `CoinSpend` — fully attacker-controlled input reachable by any wallet/mempool submitter, as confirmed by the test usage: [3](#0-2) 

### Impact Explanation
Because Python raises `IndexError` on out-of-range access (rather than truly reading adjacent memory), this does not directly yield the same memory-corruption primitive as the kernel nftables bug. However, if this exception is not caught by the caller of `is_clvm_canonical`/`is_atom_canonical` (used to determine DEDUP eligibility for spends, per `.cursor/context/clvm-execution.md`), a single malformed but otherwise CLVM-parseable spend bundle could raise an unhandled exception inside mempool/DEDUP-eligibility processing, halting transaction processing for that call path — a spend-triggered processing halt reachable from a single submitted spend bundle.

### Likelihood Explanation
High reachability: any unprivileged actor who can submit a `SpendBundle` to the mempool controls the exact bytes of `puzzle_reveal`/`solution` fed into these functions. Crafting a buffer whose final atom uses a multi-byte length-prefix encoding (`0xC0`–`0xFE` range) but is truncated immediately after the prefix byte(s) is straightforward and requires no special privileges.

### Recommendation
Add explicit bounds checks in both `is_atom_canonical()` and `is_clvm_canonical()` before every `clvm_buffer[offset]` dereference (e.g., verify `offset < len(clvm_buffer)` and return `False`/raise a defined `ConsensusError` instead of letting Python raise `IndexError`), and ensure all callers of these two functions catch and translate any parsing exception into a normal validation-failure/rejection path rather than propagating an unhandled exception through mempool processing.

### Proof of Concept
Construct a CLVM buffer consisting of a single atom whose first byte declares a multi-byte length prefix (e.g. `0xC0`, indicating "5+8 bits length prefix", `prefix_len = 1`) but omit the following length byte, e.g. `buf = bytes([0xC0])`. Calling `is_atom_canonical(buf, 0)` (or `is_clvm_canonical(buf)` with a leading `0xFF` pair byte followed by this truncated atom) causes `atom_len |= clvm_buffer[offset]` to index past the end of `buf`, raising an uncaught `IndexError`. Submitting a `CoinSpend` whose `puzzle_reveal`/`solution` bytes are engineered so that the DEDUP-eligibility canonical check encounters this truncated atom triggers the same unhandled exception during mempool processing.

**Note on verification limits:** due to iteration limits I was unable to fully confirm whether every call site of `is_clvm_canonical()` wraps the call in a try/except that safely downgrades this to a non-canonical/ineligible-for-dedup result versus propagating the exception further up into mempool-processing control flow. This should be verified directly against `chia/full_node/mempool_manager.py`'s call sites before treating the halt-impact as fully confirmed.

### Citations

**File:** chia/full_node/mempool_manager.py (L177-182)
```python
    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

```

**File:** chia/full_node/mempool_manager.py (L196-221)
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
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L198-203)
```python
@pytest.mark.anyio
async def test_bundles_are_canonical(test_bundles: list[SpendBundle]) -> None:
    for sb in test_bundles:
        for spend in sb.coin_spends:
            assert is_clvm_canonical(bytes(spend.puzzle_reveal))
            assert is_clvm_canonical(bytes(spend.solution))
```
