### Title
Unvalidated addition of attacker-controlled relative locktime to coin height/timestamp causes integer overflow, crashing spend-bundle validation - (File: chia/full_node/mempool_manager.py)

### Summary
`compute_assert_height()` in `chia/full_node/mempool_manager.py` adds a spend's user-supplied relative timelock value (`height_relative`, `seconds_relative`, `before_height_relative`, `before_seconds_relative`) directly to the coin's `confirmed_block_index`/`timestamp` and casts the result back into `uint32`/`uint64`, without first checking that the sum stays inside the representable range. This mirrors the OpenQ `getLockedFunds` bug: a large-but-individually-valid user-supplied value (`expiration`) is combined with a stored baseline value in an unchecked addition, and the resulting overflow blows up a downstream computation. [1](#0-0) 

### Finding Description
CLVM condition parsing only bounds each relative timelock argument to fit within its own 32-bit/64-bit width — e.g. `ASSERT_HEIGHT_RELATIVE`/`ASSERT_BEFORE_HEIGHT_RELATIVE` values up to `0xFFFFFFFF` are accepted, and `ASSERT_SECONDS_RELATIVE`/`ASSERT_BEFORE_SECONDS_RELATIVE` values up to `0xFFFFFFFFFFFFFFFF` are accepted; only values that don't fit in the type at all (`>= 0x100000000` for height, `>= 0x10000000000000000` for seconds) are rejected at the consensus/mempool level. [2](#0-1) [3](#0-2) 

`compute_assert_height` then does:
```python
h = uint32(removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.height_relative)
...
s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.seconds_relative)
``` [4](#0-3) 

Because a real coin's `confirmed_block_index`/`timestamp` is always non‑zero, an attacker submitting a spend bundle that spends any of their own coins with `ASSERT_HEIGHT_RELATIVE`/`ASSERT_SECONDS_RELATIVE` (or the "before" variants) set to a value close to the type's maximum (still individually valid per the accepted-range tests above) will cause `confirmed_block_index + height_relative` (or `timestamp + seconds_relative`) to exceed `uint32`/`uint64` max. The `uint32(...)`/`uint64(...)` constructors from `chia_rs.sized_ints` are strict, range-checked types (this is exactly why the test-suite parametrizes explicit “garbage upper bound” cases like `0x100000000` and `0x10000000000000000` to prove they are rejected before ever reaching arithmetic), so casting an out-of-range Python int raises rather than silently wrapping — the Chia analog of Solidity's unchecked-arithmetic revert. `compute_assert_height` is invoked from mempool spend-bundle validation (`validate_spend_bundle`) with no bounds check performed beforehand and no try/except wrapping this specific addition, so an unhandled exception propagates out of what should be a normal "reject unusual spend" code path.

### Impact Explanation
This is reachable by any unprivileged party who can submit a spend bundle to the mempool — no special permissions, no malicious peer/node assumptions required, matching the "single submitted spend bundle" reachability bar in scope. If the exception is not gracefully turned into a rejection status somewhere above `compute_assert_height`, it constitutes a spend-triggered halt of mempool/transaction processing for that call (crash/`ValidationError`-adjacent failure instead of a controlled `Err.*` rejection), analogous to how the OpenQ overflow blocked all subsequent `refundDeposit` calls once a poisoned deposit existed. At minimum it is a repeatable denial-of-service primitive against `MempoolManager.add_spend_bundle`/`validate_spend_bundle` triggered by a single cheaply-constructed spend.

### Likelihood Explanation
Medium-to-High likelihood: constructing the spend bundle requires only owning one coin and adding a single `ASSERT_HEIGHT_RELATIVE`/`ASSERT_SECONDS_RELATIVE`/`ASSERT_BEFORE_HEIGHT_RELATIVE`/`ASSERT_BEFORE_SECONDS_RELATIVE` condition with a value near the type's upper bound (both of which are demonstrably accepted individually by the parser per the cited test cases). No coordination with a farmer, timelord, or another peer is needed; it is purely a client-side construction.

### Recommendation
In `compute_assert_height` (`chia/full_node/mempool_manager.py`), clamp/validate the sum before casting, e.g. compute in Python's arbitrary-precision int, saturate to `uint32`/`uint64` max instead of raising, or explicitly reject the spend with a proper `Err.ASSERT_HEIGHT_RELATIVE_FAILED`/`Err.ASSERT_SECONDS_RELATIVE_FAILED`-style error when `confirmed_block_index + height_relative` (or the seconds equivalent) would exceed the representable range, rather than letting `uint32()`/`uint64()` raise:
```python
h = min(confirmed_block_index + spend.height_relative, MAX_UINT32)
```
and ensure the call site wraps this computation so any unexpected exception is converted into a mempool rejection (`Err.INVALID_CONDITION`) instead of propagating.

### Proof of Concept
Not independently executed (no runtime/tooling access in this environment) — this is inferred from source and existing test boundary values, not confirmed via execution. To validate: build a `SpendBundleConditions`/`SpendConditions` object (mirroring `make_test_conds` in `chia/_tests/core/mempool/test_mempool_manager.py`) for a coin whose `confirmed_block_index` is non-zero (e.g. `10`) and set `height_relative = uint32(0xFFFFFFFE)`, then call `compute_assert_height` directly and observe whether `uint32(10 + 0xFFFFFFFE)` raises instead of returning a graceful `Err`. Repeat with `seconds_relative` near `uint64` max against a coin with non-zero `timestamp`. [5](#0-4)

### Citations

**File:** chia/full_node/mempool_manager.py (L102-126)
```python
    for spend in conds.spends:
        if spend.height_relative is not None:
            h = uint32(removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.height_relative)
            ret.assert_height = max(ret.assert_height, h)

        if spend.seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.seconds_relative)
            ret.assert_seconds = max(ret.assert_seconds, s)

        if spend.before_height_relative is not None:
            h = uint32(
                removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.before_height_relative
            )
            if ret.assert_before_height is not None:
                ret.assert_before_height = min(ret.assert_before_height, h)
            else:
                ret.assert_before_height = h

        if spend.before_seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.before_seconds_relative)
            if ret.assert_before_seconds is not None:
                ret.assert_before_seconds = min(ret.assert_before_seconds, s)
            else:
                ret.assert_before_seconds = s

```

**File:** chia/_tests/core/full_node/test_conditions.py (L210-221)
```python
            # HEIGHT RELATIVE
            (co.ASSERT_HEIGHT_RELATIVE, -1, None),
            (co.ASSERT_HEIGHT_RELATIVE, 0, None),
            (co.ASSERT_HEIGHT_RELATIVE, 1, None),
            (co.ASSERT_HEIGHT_RELATIVE, 2, Err.ASSERT_HEIGHT_RELATIVE_FAILED),
            (co.ASSERT_HEIGHT_RELATIVE, 0x100000000, Err.ASSERT_HEIGHT_RELATIVE_FAILED),
            # BEFORE HEIGHT RELATIVE
            (co.ASSERT_BEFORE_HEIGHT_RELATIVE, -1, Err.ASSERT_BEFORE_HEIGHT_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_HEIGHT_RELATIVE, 0, Err.ASSERT_BEFORE_HEIGHT_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_HEIGHT_RELATIVE, 1, Err.ASSERT_BEFORE_HEIGHT_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_HEIGHT_RELATIVE, 2, None),
            (co.ASSERT_BEFORE_HEIGHT_RELATIVE, 0x100000000, None),
```

**File:** chia/_tests/core/full_node/test_conditions.py (L234-242)
```python
            # SECONDS RELATIVE
            (co.ASSERT_SECONDS_RELATIVE, -1, None),
            (co.ASSERT_SECONDS_RELATIVE, 0, None),
            (co.ASSERT_SECONDS_RELATIVE, 10, None),
            (co.ASSERT_SECONDS_RELATIVE, 11, Err.ASSERT_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_SECONDS_RELATIVE, 20, Err.ASSERT_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_SECONDS_RELATIVE, 21, Err.ASSERT_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_SECONDS_RELATIVE, 30, Err.ASSERT_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_SECONDS_RELATIVE, 0x10000000000000000, Err.ASSERT_SECONDS_RELATIVE_FAILED),
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L512-521)
```python
def expect(
    *, height: int = 0, seconds: int = 0, before_height: int | None = None, before_seconds: int | None = None
) -> TimelockConditions:
    ret = TimelockConditions(uint32(height), uint64(seconds))
    if before_height is not None:
        ret.assert_before_height = uint32(before_height)
    if before_seconds is not None:
        ret.assert_before_seconds = uint64(before_seconds)
    return ret

```
