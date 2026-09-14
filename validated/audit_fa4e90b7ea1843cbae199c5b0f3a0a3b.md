Based on my research, I found a plausible analog in [1](#0-0)  that shares the same root-cause pattern as the reported `ActivityRewardDistributor` bug: unvalidated arithmetic on externally-influenced values that are subsequently narrowed into a fixed-width integer type, with no bounds check before the operation.

### Title
Unchecked `uint32`/`uint64` overflow in `compute_assert_height()` from attacker-controlled relative timelock values - (File: `chia/full_node/mempool_manager.py`)

### Summary
`compute_assert_height()` in [2](#0-1)  adds a coin's `confirmed_block_index`/`timestamp` (both fixed-width `uint32`/`uint64`) to attacker-supplied relative timelock values (`height_relative`, `seconds_relative`, `before_height_relative`, `before_seconds_relative`) taken directly from a submitted spend bundle's conditions, then re-wraps the sum in `uint32(...)`/`uint64(...)` with no overflow guard — mirroring the reported pattern of `endTimestamp += unusedTime` overflowing after being pushed to `type(uint256).max` with no bounds check.

### Finding Description
An unprivileged wallet user / spend-bundle submitter fully controls the arguments to `ASSERT_HEIGHT_RELATIVE`, `ASSERT_SECONDS_RELATIVE`, `ASSERT_BEFORE_HEIGHT_RELATIVE`, and `ASSERT_BEFORE_SECONDS_RELATIVE` conditions. These are captured on `SpendBundleConditions.spends[i].height_relative` / `.seconds_relative` / etc. The Python helper `compute_assert_height()` computes:
```
h = uint32(removal_coin_records[coin_id].confirmed_block_index + spend.height_relative)
``` [3](#0-2)  with no check that the sum stays within the `uint32`/`uint64` range before construction. `uint32`/`uint64` from `chia_rs.sized_ints` raise on out-of-range values rather than silently truncating, so a maliciously large relative value (near `uint32`/`uint64` max) combined with any coin's confirmed height/timestamp will overflow the target type and raise an exception at construction time. This is architecturally analogous to the reported issue: a value pushed to (or near) the type's maximum, then unconditionally added to without a pre-check, causing the operation to fail instead of degrading gracefully.

Notably, the codebase's authoritative consensus path (`check_time_locks`, implemented in `chia_rs`) was already hardened with an explicit `nowrap` parameter [4](#0-3)  and [5](#0-4) , suggesting the project has previously had to specifically address wraparound/overflow bugs in exactly this height/seconds-relative computation area — reinforcing that this bug class is a recognized risk in this codebase, and that the Python-side `compute_assert_height()` (used for mempool pending-retry scheduling rather than final consensus validation) may not have received the same fix.

### Impact Explanation
If reachable without being caught, an unhandled `ValueError`/exception during `compute_assert_height()` would abort processing of the offending spend bundle at minimum. If this function is invoked from a code path that lacks exception isolation around per-spend-bundle mempool processing (e.g., pending-cache scheduling for time-locked spends), it could disrupt or halt the mempool manager's transaction-processing task, matching the accepted "spend-triggered transaction-processing halt" impact category. This is a Medium severity issue at most given it requires no privileged role — any spend bundle submitter can attempt it — but the actual blast radius (single-bundle rejection vs. mempool-wide task failure) depends on exception-handling around the specific call site, which I could not fully confirm from the index (see Uncertainty below).

### Likelihood Explanation
Low-to-Medium likelihood: it requires the attacker to craft a spend bundle with an extreme relative-timelock value on a coin they control, and to reach a code path where this computation happens outside a broad try/except. This is straightforward to construct (no special privileges, only a valid coin and clvm program), but whether it actually crashes anything durable versus being caught depends on surrounding exception handling I was unable to verify in the available index.

### Recommendation
Add explicit bounds checks (or use saturating/checked addition) before constructing `uint32`/`uint64` in `compute_assert_height()`, e.g.:
```python
h = min(uint32.MAXIMUM, removal_coin_records[...].confirmed_block_index + spend.height_relative)
```
or clamp/saturate the relative-conditions sum to the type's maximum instead of raising, and ensure this function is always called within an exception boundary that rejects only the single offending spend bundle rather than affecting broader mempool manager processing.

### Proof of Concept
1. Craft a spend bundle spending an owned coin with condition `ASSERT_HEIGHT_RELATIVE` (or `ASSERT_SECONDS_RELATIVE`) set to a value near `uint32.MAX` (`0xFFFFFFFF`) / `uint64.MAX`.
2. Submit the spend bundle to a full node's mempool via RPC/P2P as an ordinary user.
3. If the transaction reaches a point where the pending-retry path calls `compute_assert_height()`, `uint32(confirmed_block_index + height_relative)` (or the `uint64` seconds equivalent) exceeds the type's max and raises an exception at [6](#0-5) .

**Uncertainty note:** I was not able to fully trace, within index limits, every call site of `compute_assert_height()` and whether it is always wrapped in exception handling that limits the blast radius to the single spend bundle (vs. crashing the async task processing new transactions more broadly). I also could not confirm from the index whether `chia_rs.sized_ints.uint32`/`uint64` raise `ValueError` on overflow versus silently wrapping — this is based on standard chia-blockchain conventions rather than direct inspection of the `chia_rs` binding source, which is outside the indexed Python codebase. A Devin session with full repository/dependency access could confirm the exact `chia_rs` overflow behavior and trace all call sites of `compute_assert_height()` to determine the precise blast radius.

### Citations

**File:** chia/full_node/mempool_manager.py (L12-25)
```python
from chia_rs import (
    ELIGIBLE_FOR_DEDUP,
    ELIGIBLE_FOR_FF,
    MEMPOOL_MODE,
    BLSCache,
    CoinRecord,
    ConsensusConstants,
    SpendBundle,
    SpendBundleConditions,
    check_time_locks,
    get_flags_for_height_and_constants,
    supports_fast_forward,
    validate_clvm_and_signature,
)
```

**File:** chia/full_node/mempool_manager.py (L82-127)
```python
def compute_assert_height(
    removal_coin_records: dict[bytes32, CoinRecord],
    conds: SpendBundleConditions,
) -> TimelockConditions:
    """
    Computes the most restrictive height- and seconds assertion in the spend bundle.
    Relative heights and times are resolved using the confirmed heights and
    timestamps from the coin records.
    """

    ret = TimelockConditions()
    ret.assert_height = uint32(conds.height_absolute)
    ret.assert_seconds = uint64(conds.seconds_absolute)
    ret.assert_before_height = (
        uint32(conds.before_height_absolute) if conds.before_height_absolute is not None else None
    )
    ret.assert_before_seconds = (
        uint64(conds.before_seconds_absolute) if conds.before_seconds_absolute is not None else None
    )

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

    return ret
```

**File:** chia/consensus/block_body_validation.py (L571-578)
```python
    if conds is not None:
        error: int | None = check_time_locks(
            removal_coin_records,
            conds,
            prev_transaction_block_height,
            prev_transaction_block_timestamp,
            nowrap=(prev_transaction_block_height >= constants.HARD_FORK2_HEIGHT),
        )
```
