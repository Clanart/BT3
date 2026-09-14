### Title
Out-of-bounds/unhandled-exception read in CLVM atom length-prefix parsing during spend bundle admission - (File: `chia/full_node/mempool_manager.py`)

### Summary
The CVE describes an out-of-bounds read in `concat_hash_string` in nDPI's `ssh.c`, caused by parsing an attacker-controlled length-prefixed field without validating that the declared length/offset stays within the buffer bounds. The Chia codebase contains an analogous unguarded, attacker-reachable, length-prefix parsing routine: `is_atom_canonical()` in `chia/full_node/mempool_manager.py`, which reads bytes at increasing offsets into a CLVM buffer supplied directly from a submitted spend bundle's `puzzle_reveal`/`solution`, without ever checking `offset` against `len(clvm_buffer)`.

### Finding Description
`is_atom_canonical()` decodes the multi-byte CLVM atom length prefix by repeatedly indexing into `clvm_buffer` based on a `prefix_len` derived from the buffer's own first byte, and never checks that `offset` stays within the buffer: [1](#0-0) 

This is called for every coin spend from `is_clvm_canonical()`, which walks the entire buffer token-by-token (again indexing `clvm_buffer[offset]` with no bounds check other than a final equality check after the loop already terminated or crashed): [2](#0-1) 

Both functions are invoked directly on attacker-controlled bytes taken straight from the coin spend inside `MempoolManager.validate_spend_bundle()`, the core admission path executed for every spend bundle a client submits to the mempool: [3](#0-2) 

If a crafted `puzzle_reveal` or `solution` ends with a truncated multi-byte atom length prefix (e.g., a byte such as `0xFC`/`0xFE`-style prefix class indicating a 5-byte length continuation, placed at or near the end of the buffer), the `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` loop in `is_atom_canonical` will index past the end of `clvm_buffer`. In Python, indexing a `bytes` object out of range raises `IndexError` rather than causing memory corruption, but this is still an unguarded/uncontrolled read of attacker-influenced state that the CVE's bug class (missing bounds check before consuming a length-prefixed field) directly maps to.

### Impact Explanation
An unhandled `IndexError` raised from `is_atom_canonical`/`is_clvm_canonical` inside `validate_spend_bundle()` is not explicitly caught anywhere in that function or in `add_spend_bundle()`. If this exception propagates uncaught up through the full node's spend-bundle handling task, it can abort mempool processing for that request, providing a spend-triggered path to disrupt transaction processing (a crash/DoS on the specific code path) using nothing but a single, cheaply-crafted, unsigned spend bundle submitted by any wallet/RPC/peer client. This matches the "spend-triggered transaction-processing halt" acceptance criterion.

I was not able to fully confirm, within the available search iterations, whether an outer `except Exception` wrapper in `chia/full_node/full_node.py`'s transaction-handling RPC/message path (e.g., around `add_spend_bundle`/`add_transaction`) catches and safely converts this exception before it can affect the node process or mempool state; `full_node.py` does contain `except Exception` blocks in the vicinity of transaction handling, so this may already be defused into a normal error response rather than a genuine halt. This uncertainty affects the severity: if caught, this is at most a per-request validation failure (low severity, out of scope per the exclusion of "no-impact analogs"); if uncaught, it could disrupt mempool processing for that call chain.

### Likelihood Explanation
Triggering requires only submitting a single spend bundle with a `puzzle_reveal` or `solution` whose serialized CLVM bytes end in a truncated multi-byte atom length prefix — this is trivial to construct and requires no privileges, matching the reachable actor set (unprivileged spend-bundle submitter). However, likelihood of meaningful impact is reduced by the uncertainty about whether the exception is already caught by an outer handler in the RPC/protocol message dispatch layer.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (verifying `offset < len(clvm_buffer)` both for the initial prefix byte reads and for the final `1 + prefix_len + atom_len` computation), and treat any out-of-bounds condition as "not canonical" (return `False`) rather than allowing an exception to propagate. Apply the same defensive check in `is_clvm_canonical()`'s main loop. Additionally, verify/ensure that `MempoolManager.validate_spend_bundle()` and its callers wrap CLVM-buffer parsing in a `try/except` that converts any parsing exception into `Err.INVALID_COIN_SOLUTION` (consistent with the existing non-canonical rejection path) instead of relying on it never raising.

### Proof of Concept
Conceptual PoC (illustrating the missing bounds check; requires confirming call-path exception handling to determine full impact):
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# A single byte selecting the 5-continuation-byte length-prefix class
# (b & 0b11111110 == 0b11111100), with no continuation bytes present.
truncated_atom_prefix = bytes([0b11111101])  # or similar prefix class byte, buffer length 1

is_clvm_canonical(truncated_atom_prefix)  # raises IndexError inside is_atom_canonical
```
Embedding such a buffer as a coin spend's `puzzle_reveal` or `solution` bytes and submitting it as a `SpendBundle` drives execution into `chia.full_node.mempool_manager.MempoolManager.validate_spend_bundle()` at [3](#0-2)  which calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))`, reaching the unguarded indexing in `is_atom_canonical` at [4](#0-3) .

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
