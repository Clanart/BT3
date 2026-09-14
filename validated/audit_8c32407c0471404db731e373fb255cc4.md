### Title
Removal of the per-opcode 1024 announcement/message cap in hard-fork 3.0 allows unbounded chained announcement/message conditions, causing a spend-triggered mempool validation-time DoS - (File: `chia/consensus/condition_costs.py`, `chia/full_node/mempool_manager.py`)

### Summary
Prior to hard-fork 3.0, Chia capped the number of `CREATE_COIN_ANNOUNCEMENT`, `CREATE_PUZZLE_ANNOUNCEMENT`, `ASSERT_COIN_ANNOUNCEMENT`, `ASSERT_PUZZLE_ANNOUNCEMENT`, `ASSERT_CONCURRENT_SPEND`, `ASSERT_CONCURRENT_PUZZLE`, `SEND_MESSAGE`, and `RECEIVE_MESSAGE` conditions per opcode to 1024 per spend, enforced with `Err.TOO_MANY_ANNOUNCEMENTS`. This is directly analogous to the CVE-2023-23916 bug class: a per-item resource cap enforced at too fine a granularity (per-header in curl's case, per-opcode-per-spend here), which can be trivially bypassed by "chaining" more of the same cheap items than the cap-holder anticipated.

### Finding Description
The test suite confirms the per-opcode cap was removed starting with hard-fork 3.0 and replaced solely by CLVM cost metering: [1](#0-0) 

In its place, each announcement/message condition is now only charged a flat `MESSAGE_CONDITION_COST = 700`: [2](#0-1) 

Given `max_tx_clvm_cost = MAX_BLOCK_COST_CLVM // 2 = 5,500,000,000`, a single spend bundle can carry roughly 5,500,000,000 / 700 ≈ 7.8 million announcement/message conditions while staying within the per-transaction cost budget: [3](#0-2) 

The existing benchmark test for just 1024 duplicate announcements — the *old* cap value — already tolerates up to 14 seconds of processing time, with an explicit TODO acknowledging the current implementation is far from the intended <1 second: [4](#0-3) 

Because `pre_validate_spendbundle()` runs the expensive Rust-side condition/announcement validation to completion in the worker pool *before* checking `validation_timeout`, the CPU/memory cost is incurred regardless of whether the spend bundle is ultimately rejected for taking too long: [5](#0-4) 

This mirrors the curl "malloc bomb" bug precisely: a coarse historical cap (curl's overall decompression-chain cap; Chia's 1024-per-opcode cap) was defeated/removed while the underlying per-unit cost model (`MESSAGE_CONDITION_COST=700`) is not calibrated to the true validation cost of processing many chained announcement/message conditions (hashing, insertion into announcement sets, matching against asserts) — allowing a single attacker-controlled input to multiply cheap-per-unit operations into disproportionate aggregate resource consumption.

### Impact Explanation
An unprivileged wallet user or spend-bundle submitter can craft a single spend bundle (well within `max_tx_clvm_cost`) containing millions of `CREATE_COIN_ANNOUNCEMENT`/`SEND_MESSAGE`/etc. conditions. Based on the benchmark data (1024 such conditions already taking up to ~14s of processing headroom), scaling to ~7.8 million conditions could consume proportionally excessive CPU time on the full node's validation worker pool (`MempoolManager.pool`), for a single submission. Since `validate_clvm_and_signature` runs to completion before the `validation_timeout` bail-out is even checked, the resource cost is paid by the node regardless of rejection, enabling a "spend-triggered transaction-processing halt" that can stall mempool worker threads and delay processing of legitimate transactions for other users — a Medium/High DoS analog of the curl CVE's resource-without-limits class.

### Likelihood Explanation
Likelihood is Medium: the input is fully within reach of any unprivileged actor able to submit a spend bundle (no special privileges, no malicious peer/farmer/node assumption), and the removal of the 1024-per-opcode cap plus low flat per-condition cost are both directly visible/confirmed in the current codebase and its own tests/comments (the "TODO: optimize clvm to make this run in < 1 second" note indicates the maintainers are aware this construct is already slow at 1024 items, let alone millions).

### Recommendation
- Reintroduce an aggregate (not just cost-based) cap on the total number of announcement/message conditions per spend or per spend bundle, independent of `MESSAGE_CONDITION_COST`, or substantially raise `MESSAGE_CONDITION_COST` so it reflects true validation/memory cost at scale (including any O(n log n) or worse announcement-matching cost).
- Move the `validation_timeout` check earlier, e.g. via incremental/interruptible validation or a hard wall-clock/cost-rate limit enforced *during* Rust-side `validate_clvm_and_signature`, not only after it completes.
- Add a regression test that exercises the new uncapped condition count (e.g., hundreds of thousands to millions of message/announcement conditions) to measure and bound real single-bundle validation latency under `MEMPOOL_MODE`.

### Proof of Concept
1. Deploy against a node past the hard-fork-3.0 activation height (where the `TOO_MANY_ANNOUNCEMENTS` opcode cap no longer applies, per `test_announce_conditions_limit`'s `limit_consensus_modes` restriction).
2. Construct a CLVM solution for a single coin spend that emits on the order of several million `(66 0x3f {msg} {coin})` (`SEND_MESSAGE`) or `(60 'test')` (`CREATE_COIN_ANNOUNCEMENT`) conditions, sized so total cost (`num_conditions * MESSAGE_CONDITION_COST + byte_cost`) stays under `max_tx_clvm_cost` (5,500,000,000).
3. Submit the resulting `SpendBundle` to `MempoolManager.pre_validate_spendbundle()`/`add_transaction()` via the standard wallet/RPC transaction submission path.
4. Measure wall-clock time consumed in the `pool.run_in_loop(validate_clvm_and_signature, ...)` call; extrapolating from the existing benchmark (1024 conditions ≈ up to 14s ceiling), a payload with ~7.6 thousand times as many conditions is expected to consume disproportionately large CPU time on the node's validation worker, before the post-hoc `validation_timeout` rejection occurs, tying up mempool worker capacity for other submitters.

**Uncertainty note**: The actual per-condition validation cost (hashing/matching announcements) is implemented in the Rust `chia_rs` crate, which is outside this repository's indexed Python/CLSP sources; I could not directly inspect its algorithmic complexity to confirm whether it is linear or worse in the number of announcements. The analysis above is based on the Python-side test benchmarks, cost constants, and mempool admission code, which strongly suggest the underlying cost model is unbalanced relative to the removed cap, but the precise scaling factor should be validated empirically by a background agent with code-execution access.

### Citations

**File:** chia/_tests/core/full_node/test_conditions.py (L369-384)
```python
    @pytest.mark.limit_consensus_modes(
        allowed=[ConsensusMode.HARD_FORK_2_0], reason="announce conditions limit was removed in hard-fork 3.0"
    )
    async def test_announce_conditions_limit(
        self,
        consensus_mode: ConsensusMode,
        prefix: str,
        condition1: str,
        condition2: str,
        num: int,
        expect_err: Err | None,
        bt: BlockTools,
    ) -> None:
        """
        Test that the condition checker accepts more announcements than the new per puzzle limit
        pre-v2-softfork, and rejects more than the announcement limit afterward.
```

**File:** chia/consensus/condition_costs.py (L6-19)
```python
class ConditionCost(Enum):
    # Condition Costs
    AGG_SIG = 1200000  # the cost of one G1 subgroup check + aggregated signature validation
    CREATE_COIN = 1800000

    # with hard fork 2 (Chia 3.0) all spends have a cost
    SPEND_COST = CREATE_COIN // 4
    NEW_CREATE_COIN = CREATE_COIN - SPEND_COST

    # with hard fork 2 (Chia 3.0) all conditions have a cost
    # SEND/RECEIVE MESSAGE and CREATE/ASSERT ANNOUNCEMENT
    MESSAGE_CONDITION_COST = 700
    # all other conditions
    GENERIC_CONDITION_COST = 200
```

**File:** chia/full_node/mempool_manager.py (L374-378)
```python

        self.max_block_clvm_cost = uint64(self.constants.MAX_BLOCK_COST_CLVM - BLOCK_OVERHEAD)
        self.max_tx_clvm_cost = (
            max_tx_clvm_cost if max_tx_clvm_cost is not None else uint64(self.constants.MAX_BLOCK_COST_CLVM // 2)
        )
```

**File:** chia/full_node/mempool_manager.py (L549-587)
```python
        try:
            flags = get_flags_for_height_and_constants(self.peak.height, self.constants)
            sbc: SpendBundleConditions
            sbc, new_cache_entries, duration = await self.pool.run_in_loop(
                validate_clvm_and_signature,
                spend_bundle,
                self.max_tx_clvm_cost,
                self.constants,
                flags | MEMPOOL_MODE,
                nice=(5, -fee_per_cost),
            )
        # validate_clvm_and_signature raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValidationError(error)
            else:
                raise ValidationError(Err.UNKNOWN)  # pragma: no cover
        finally:
            self._worker_queue_size -= 1

        if sbc.num_atoms > sbc.cost * 60_000_000 / self.constants.MAX_BLOCK_COST_CLVM:
            raise ValueError("too many atoms")

        if sbc.num_pairs > sbc.cost * 60_000_000 / self.constants.MAX_BLOCK_COST_CLVM:
            raise ValueError("too many pairs")

        if spend_bundle_id is None:
            spend_bundle_id = spend_bundle.name()

        if duration > self.validation_timeout:
            self._maybe_log_timeout_spend_bundle(spend_bundle_id, spend_bundle)
            raise ValueError(f"timeout {duration:0.4} s")

        cost = sbc.execution_cost + sbc.condition_cost
        if cost == 0 or (duration > 0.1 and duration * 1e9 / cost > self.validation_timeout * 5.0):
            self._maybe_log_timeout_spend_bundle(spend_bundle_id, spend_bundle)
            raise ValueError(f"timeout ({duration * 1e9 / cost:0.4} ns/cost)")
```

**File:** chia/_tests/core/mempool/test_mempool.py (L2856-2872)
```python
    @pytest.mark.parametrize(
        "opcode", [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT]
    )
    def test_duplicate_coin_announces(
        self, opcode: ConditionOpcode, softfork_height: uint32, benchmark_runner: BenchmarkRunner
    ) -> None:
        # we only allow 1024 create- or assert announcements per spend
        condition = CREATE_ANNOUNCE_COND.format(opcode=opcode.value[0], num=1024)

        with benchmark_runner.assert_runtime(seconds=14):
            npc_result = generator_condition_tester(condition, quote=False, height=softfork_height)

        assert npc_result.error is None
        assert npc_result.conds is not None
        assert len(npc_result.conds.spends) == 1
        # coin announcements are not propagated to python, but validated in rust
        # TODO: optimize clvm to make this run in < 1 second
```
