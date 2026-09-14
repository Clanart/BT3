Note: `check_time_locks` itself is implemented in the compiled `chia_rs` Rust extension, which is not present in this Python repo's index, so I cannot cite its internal arithmetic (checked/wrapping add) directly. I can only confirm from the Python call sites how `nowrap` is wired up and that it is currently disabled by default (since `HARD_FORK2_HEIGHT` is a far-future placeholder).

### Title
Potential integer-overflow bypass of relative time-lock conditions via unenforced `nowrap` flag before HARD_FORK2 - ([File: chia/full_node/mempool_manager.py], [File: chia/consensus/block_body_validation.py])

### Summary
The reported Solidity bug is a classic "unchecked timestamp addition" pattern: `block.timestamp + challengePeriod` can wrap, letting a party make time-based comparisons permanently true or false, freezing execution. Chia's own consensus/mempool code contains an analogous, explicitly-named guard for this exact bug class: `check_time_locks(..., nowrap=...)`. Both call sites compute `nowrap` as `height >= constants.HARD_FORK2_HEIGHT`, and `HARD_FORK2_HEIGHT` defaults to the placeholder sentinel `0xFFFFFFFA` [1](#0-0) , which is not yet reached on any live network. This means `nowrap=False` is the operative mode today for both full-node block validation and mempool admission.

### Finding Description
Relative time-lock conditions (`ASSERT_SECONDS_RELATIVE`, `ASSERT_BEFORE_SECONDS_RELATIVE`, and their height counterparts) are computed by adding a user-supplied CLVM argument to a coin's recorded confirmation timestamp/height: [2](#0-1) [3](#0-2) 

The mempool admission path passes this computed value into `check_time_locks` with an explicit `nowrap` flag: [4](#0-3) 

The full-node block-body validation path does the same: [5](#0-4) 

Both compute `nowrap` from `HARD_FORK2_HEIGHT`, which is currently the sentinel value `0xFFFFFFFA` in `DEFAULT_CONSTANTS` [6](#0-5) , i.e., effectively "never" on real chains today. Test fixtures explicitly document that setting `HARD_FORK2_HEIGHT=0` changes the semantics of `check_time_locks`, and the dedicated `nowrap=True` unit tests only assert correctness when the flag is forced on, not in the currently-live `nowrap=False` mode [7](#0-6) . Since `ASSERT_SECONDS_RELATIVE`/`ASSERT_HEIGHT_RELATIVE` opcodes take fully attacker-controlled 64-bit integer arguments straight from the spend bundle's CLVM conditions (as exercised in `test_condition` with values up to `0x10000000000000000`) [8](#0-7) , any wallet user or spend-bundle submitter can supply a huge relative time-lock argument near `2**64`, which is added to a real coin's timestamp/height. If the underlying `chia_rs::check_time_locks` implementation performs this addition without saturating/checked arithmetic when `nowrap=False` (as its name and the dedicated flag strongly imply, mirroring the reported `challengePeriod` overflow), the result wraps to a small or otherwise incorrect value, causing the relative time-lock assertion to evaluate against an unintended threshold instead of the intended (effectively unsatisfiable or always-satisfiable) value.

I could not directly inspect the Rust arithmetic inside `check_time_locks` (it ships as a compiled `chia_rs` extension and is outside this Python repo's indexed source), so I cannot confirm with certainty whether the wrap actually occurs versus being silently clamped by `chia_rs` internals independent of the `nowrap` flag's naming. This is the key uncertainty in this analog.

### Impact Explanation
If the wraparound is real, an attacker-crafted spend bundle with a maliciously large relative timelock argument could:
- Cause a coin's intended clawback/lock condition (e.g., a lock meant to force minimum lock time before spend) to be trivially bypassed, permitting premature/unauthorized coin movement, or
- Cause the relative assertion to always fail unexpectedly, effectively freezing legitimate spends that rely on that condition (a spend-triggered transaction-processing halt), matching the "freezing" impact class of the original report.

### Likelihood Explanation
Reachability is trivial: any spend-bundle submitter fully controls the CLVM condition arguments in their own coin spend, including `ASSERT_SECONDS_RELATIVE`/`ASSERT_HEIGHT_RELATIVE` values, so no privileged role is required. However, likelihood of actual exploitability is unresolved because the presence of `nowrap` naming strongly suggests intentional wrap-vs-no-wrap semantics were designed for, and might already be safely handled inside `chia_rs` (e.g., via `wrapping_add` with an intentional design rationale documented elsewhere, or via bounds validation on the CLVM operand parsing before it reaches `check_time_locks`). Without access to the `chia_rs` source, this cannot be confirmed as an actual live bug versus dead/inert flag plumbing.

### Recommendation
- Confirm in `chia_rs` (outside this repo) whether `check_time_locks` uses checked/saturating arithmetic when `nowrap=False`, and if not, make wrap-safety unconditional rather than gated behind `HARD_FORK2_HEIGHT`.
- Add explicit bounds validation on relative time-lock/height condition arguments during CLVM condition parsing so that additions against `confirmed_block_index`/`timestamp` can never overflow regardless of the `nowrap` flag's value.
- Add unit tests exercising `check_time_locks(..., nowrap=False)` with near-`u64`-max relative arguments to directly verify wraparound behavior before `HARD_FORK2_HEIGHT` activates.

### Proof of Concept
Not fully constructible from available context: reproducing requires invoking the compiled `chia_rs.check_time_locks` with `nowrap=False` and a coin record plus a `SpendBundleConditions` containing `seconds_relative` set near `2**64 - 1` (as parsed from a spend with `(ASSERT_SECONDS_RELATIVE 0xFFFFFFFFFFFFFFF0)` similar to the test pattern in `test_condition`) [8](#0-7) , then checking whether the resulting `assert_seconds` computed in `compute_assert_height` [2](#0-1)  wraps to a small value. This final verification step requires running the compiled `chia_rs` extension, which is unavailable through static code search alone.

### Citations

**File:** chia/consensus/default_constants.py (L79-83)
```python
    POOL_SUB_SLOT_ITERS=uint64(37600000000),  # iters limit * NUM_SPS
    # June 2024
    HARD_FORK_HEIGHT=uint32(5496000),
    # TODO: todo_v2_plots finalize fork height
    HARD_FORK2_HEIGHT=uint32(0xFFFFFFFA),
```

**File:** chia/full_node/mempool_manager.py (L107-109)
```python
        if spend.seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.seconds_relative)
            ret.assert_seconds = max(ret.assert_seconds, s)
```

**File:** chia/full_node/mempool_manager.py (L120-125)
```python
        if spend.before_seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.before_seconds_relative)
            if ret.assert_before_seconds is not None:
                ret.assert_before_seconds = min(ret.assert_before_seconds, s)
            else:
                ret.assert_before_seconds = s
```

**File:** chia/full_node/mempool_manager.py (L864-871)
```python
        assert self.peak.timestamp is not None
        tl_error_rust: int | None = check_time_locks(
            removal_record_dict,
            conds,
            self.peak.height,
            self.peak.timestamp,
            nowrap=self.peak.height >= self.constants.HARD_FORK2_HEIGHT,
        )
```

**File:** chia/consensus/block_body_validation.py (L569-581)
```python
    # 21. Verify conditions
    # verify absolute/relative height/time conditions
    if conds is not None:
        error: int | None = check_time_locks(
            removal_coin_records,
            conds,
            prev_transaction_block_height,
            prev_transaction_block_timestamp,
            nowrap=(prev_transaction_block_height >= constants.HARD_FORK2_HEIGHT),
        )
        if error is not None:
            # TODO: standardise errors across Rust and Python so cast is not necesary here
            return Err(error)
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L493-509)
```python
    def test_conditions(
        self,
        conds: SpendBundleConditions,
        expected: Err | None,
    ) -> None:
        res: int | None = check_time_locks(
            dict(self.REMOVALS),
            conds,
            self.PREV_BLOCK_HEIGHT,
            self.PREV_BLOCK_TIMESTAMP,
            nowrap=True,
        )
        e: Err | None = None
        if res is not None:
            # TODO: remove when Rust errors and Python Errors are the same
            e = Err(res)
        assert e == expected
```

**File:** chia/_tests/core/full_node/test_conditions.py (L234-251)
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
            # BEFORE SECONDS RELATIVE
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, -1, Err.ASSERT_BEFORE_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 0, Err.ASSERT_BEFORE_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 10, Err.ASSERT_BEFORE_SECONDS_RELATIVE_FAILED),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 11, None),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 20, None),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 21, None),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 30, None),
            (co.ASSERT_BEFORE_SECONDS_RELATIVE, 0x100000000000000, None),
```
