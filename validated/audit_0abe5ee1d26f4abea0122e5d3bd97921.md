### Title
Unbounded synchronous CLVM execution in wallet Offer parsing blocks the wallet's single-threaded event loop - ([File: chia/wallet/trading/offer.py])

### Summary
The Calico Typha advisory describes a TLS handshake executed synchronously in the server's single main loop with no timeout, letting one client stall the loop and deny service to all other connections. The analogous pattern in this codebase is `Offer.__post_init__()` / `Offer._get_offered_coins()` in `chia/wallet/trading/offer.py`, which run attacker/counterparty-supplied CLVM (puzzle reveals + solutions from an offer file) synchronously on the wallet service's asyncio event loop, with no dedicated worker thread/process and no per-call timeout — unlike the mempool's `pre_validate_spendbundle()`, which explicitly offloads CLVM execution to a thread pool (`MempoolManager.pool`) and enforces `validation_timeout`.

### Finding Description
When a wallet parses an offer (`Offer.from_bytes`/`from_bech32m`, reachable via wallet RPC endpoints such as `get_offer_summary`/`check_offer_validity`/`take_offer` in `chia/wallet/wallet_rpc_api.py`), `Offer.__post_init__` iterates over every coin spend in the (counterparty-controlled) bundle and calls `compute_spend_hints_and_additions(cs, max_cost=max_cost)` directly on the calling coroutine/thread [1](#0-0) . Separately, `Offer._get_offered_coins()` runs the inner puzzle for each parent spend via `inner_puzzle.run_with_cost(cost_left, inner_solution)`, again synchronously and starting the shared cost budget at `INFINITE_COST` [2](#0-1) .

`Program.run_with_cost()`/`_run()` call directly into the Rust CLVM interpreter (`run_chia_program`) on the calling thread with no timeout mechanism of its own [3](#0-2) . In contrast, the mempool's admission path deliberately isolates this exact class of CLVM work: `pre_validate_spendbundle()` executes `validate_clvm_and_signature` via `self.pool.run_in_loop(...)` in a dedicated thread pool and rejects if `duration > self.validation_timeout` [4](#0-3) . The design documentation for this module explicitly calls out this pattern as intentional DoS protection ("Duration guard: >2s validation time → reject") for mempool validation [5](#0-4)  — but no equivalent guard, thread-pool isolation, or timeout exists for offer parsing on the wallet side.

Because the wallet service (like Typha's connection-handling loop) runs a single asyncio event loop that serially services RPC calls, wallet sync, and other coroutines, any synchronous, CPU-bound call that isn't dispatched to a worker thread blocks all other wallet activity for its duration. Offers are exchanged out-of-band (files, DEXes, mempool-independent) from arbitrary, unauthenticated counterparties, so a wallet user opening/summarizing/validating a malicious offer is directly exposed to this blocking call before any admission or fee-based gating applies (offers are not spend-bundle-cost-gated the way mempool admission is).

### Impact Explanation
While CLVM execution cost is bounded (`MAX_BLOCK_COST_CLVM`, ~11e9 cost units) and Rust CLVM execution is fast in the common case, the offer-parsing code path builds/traverses spend hints and runs puzzles for every coin spend in the bundle with a shared, effectively unbounded (`INFINITE_COST`) budget per top-level call, with no timeout, no cancellation, and no dedicated executor. A crafted offer (deeply nested/expensive puzzle reveals or many coin spends) can consume a large fraction of the maximum per-block CLVM budget synchronously inside the wallet's single event-loop thread, stalling wallet RPC responsiveness, wallet syncing, and other pending coroutines for the duration of that single call — a spend/offer-triggered processing halt analogous to the Typha TLS handshake blocking its accept loop. This is a service-availability issue for the local wallet process, not a consensus-state or fund-safety compromise.

### Likelihood Explanation
Any offer counterparty or wallet RPC caller can supply an arbitrary offer file/bech32m blob for parsing; no prior trust or fee is required to have the wallet execute the embedded puzzles via `get_offer_summary`/`check_offer_validity`/`take_offer`. The attack requires only crafting a bundle with a costly-but-under-budget inner puzzle (or many coin spends) — a low bar for an unprivileged actor, though the exact severity depends on how much CPU time an 11e9-cost-unit CLVM execution actually consumes in practice, which was not independently benchmarked in this investigation.

### Recommendation
Move offer/puzzle CLVM execution performed during untrusted-input parsing (`Offer.__post_init__`, `Offer._get_offered_coins`, and any other synchronous `run`/`run_with_cost` calls on counterparty-supplied puzzles) off the wallet's main event loop and into a bounded worker (thread/process pool), applying an explicit wall-clock timeout analogous to `MempoolManager.validation_timeout`, and consider capping per-offer CLVM cost budgets independent of `MAX_BLOCK_COST_CLVM`.

### Proof of Concept
Not independently reproduced in this investigation — this analysis is based on static code review of `chia/wallet/trading/offer.py`, `chia/types/blockchain_format/program.py`, and the mempool's contrasting timeout/thread-pool design in `chia/full_node/mempool_manager.py`. A concrete PoC would need to construct an offer whose coin spends' inner puzzles are engineered to consume a large fraction of `MAX_BLOCK_COST_CLVM` while remaining under the cost checks in `__post_init__`, then measure wall-clock stall of the wallet RPC service while `get_offer_summary`/`check_offer_validity` processes it — this was not executed/timed as part of this review.

### Citations

**File:** chia/wallet/trading/offer.py (L162-184)
```python
        # populate the _additions cache
        adds: dict[Coin, list[Coin]] = {}
        hints: dict[bytes32, bytes32] = {}
        max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
        for cs in self._bundle.coin_spends:
            # you can't spend the same coin twice in the same SpendBundle
            assert cs.coin not in adds
            try:
                hinted_coins, cost = compute_spend_hints_and_additions(cs, max_cost=max_cost)
                max_cost -= cost
                adds[cs.coin] = [hc.coin for hc in hinted_coins.values()]
                hints = {**hints, **{id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None}}
            except ValidationError:
                raise
            except ValueError as e:
                if e.args and e.args[0] == "cost exceeded or below zero":
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend") from e
                continue
            except Exception:
                continue
            if max_cost < 0:
                raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend")
        object.__setattr__(self, "_additions", adds)
```

**File:** chia/wallet/trading/offer.py (L244-266)
```python
    def _get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        offered_coins: dict[bytes32 | None, list[Coin]] = {}

        cost_left = INFINITE_COST
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None

                # We're going to look at the conditions created by the inner puzzle
                puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
                assert cost_left >= puzzle_cost
                cost_left -= puzzle_cost
                expected_num_matches: int = 0
```

**File:** chia/types/blockchain_format/program.py (L144-156)
```python
    def _run(self, max_cost: int, flags: int, args: Any) -> tuple[int, Program]:
        prog_args = Program.to(args)
        cost, r = run_chia_program(self.as_bin(), prog_args.as_bin(), max_cost, flags)
        return cost, Program.to(r)

    def run_with_cost(self, max_cost: int, args: Any, flags=DEFAULT_FLAGS) -> tuple[int, Program]:
        # when running puzzles in the wallet, default to enabling all soft-forks
        # as well as enabling mempool-mode (i.e. strict mode)
        return self._run(max_cost, flags, args)

    def run(self, args: Any, max_cost=INFINITE_COST, flags=DEFAULT_FLAGS) -> Program:
        _cost, r = self._run(max_cost, flags, args)
        return r
```

**File:** chia/full_node/mempool_manager.py (L548-587)
```python
        self._worker_queue_size += 1
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

**File:** .cursor/context/mempool.md (L91-96)
```markdown
    height/timestamp

11. **Impossible constraints**: `assert_before_height ≤ assert_height` → reject permanently

12. **Duration guard**: >2s validation time → reject (DoS protection)

```
