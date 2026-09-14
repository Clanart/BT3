## Finding: Unbounded CLVM Execution in Offer Coin Identification Allows Memory-Exhaustion DoS

### Title
Unbounded CLVM Cost in `Offer._get_offered_coins()` Enables Memory-Exhaustion DoS via Crafted Offer File - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer._get_offered_coins()` executes each inner puzzle of an untrusted offer's coin spends with `cost_left = INFINITE_COST` [1](#0-0) , meaning puzzle execution is not bounded by `MAX_BLOCK_COST_CLVM` the way every other CLVM execution path in the codebase is. A malicious offer counterparty can craft a `puzzle_reveal`/`solution` pair whose inner puzzle recursively/iteratively produces an extremely large number of CLVM atoms/pairs (e.g., deeply nested conditions or huge lists), and any wallet operation that inspects the offer (summary, offered-amounts, pending-amounts) will run that puzzle to completion with no cost ceiling, exhausting memory — directly analogous to the reported Slic3r `PerimeterGenerator` flaw where a crafted input file drives unbounded internal processing/memory growth.

### Finding Description
Every other CLVM execution surface in this codebase enforces a hard cost ceiling before or during execution:
- Mempool admission: `pre_validate_spendbundle()` runs with `self.max_tx_clvm_cost` and additionally checks `num_atoms`/`num_pairs` against a cost-proportional threshold [2](#0-1) .
- Offer additions/hints computation in the same file's `__post_init__` bounds cost via `max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)`, decrementing per spend and raising `ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, ...)` if exceeded [3](#0-2) .
- `Offer.conditions()` likewise uses a bounded `max_cost` and raises on overflow [4](#0-3) .

However, `Offer._get_offered_coins()` deviates from this pattern: it initializes `cost_left = INFINITE_COST` and runs each matched inner puzzle with `inner_puzzle.run_with_cost(cost_left, inner_solution)` [5](#0-4) . Because `cost_left` starts at `INFINITE_COST`, the very first untrusted spend's inner puzzle can consume arbitrarily large CLVM cost — and therefore allocate arbitrarily many atoms/pairs in memory — before any bound is applied. The `assert cost_left >= puzzle_cost` check only prevents underflow after the fact; it does nothing to cap resource usage during execution since the starting budget is effectively unlimited.

`_get_offered_coins()` is memoized behind `get_offered_coins()` [6](#0-5)  and is transitively invoked by wallet-facing offer inspection methods that a wallet user or RPC caller triggers on an attacker-supplied offer:
- `get_offered_amounts()` → `summary()` (JSON summary of an offer, typically called before a user accepts/takes an offer) [7](#0-6) 
- `get_pending_amounts()` [8](#0-7) 

These are reachable from `chia/wallet/wallet_rpc_api.py`, `chia/wallet/trade_manager.py`, and CLI/RPC offer-inspection flows (`chia/cmds/wallet_funcs.py`), all of which are the standard code path a wallet uses to look at an offer file received from an untrusted counterparty before deciding whether to accept it.

### Impact Explanation
An attacker who crafts a malicious offer file (an untrusted, attacker-controlled input analogous to the "specially crafted STL file" in the CVE) can cause any wallet that merely inspects the offer (e.g., calls `summary()` to display it to the user, which happens automatically in normal offer-review UX) to run CLVM with no memory/cost bound. This can exhaust the wallet process's available memory, causing a crash or system-wide resource exhaustion — a spend-triggered (here, offer-triggered) transaction-processing halt on the victim's wallet node, without requiring the victim to actually accept or sign the offer.

### Likelihood Explanation
Likelihood is high for any wallet user who receives and inspects offer files from arbitrary/unknown counterparties, which is the normal offer workflow in Chia (offers are commonly shared via files/links from untrusted third parties). No signature or fee payment is required to trigger the bug — merely loading and summarizing the offer (`Offer.from_bytes` → `summary()`/`get_offered_amounts()`) is sufficient to invoke the unbounded execution path.

### Recommendation
Bound `_get_offered_coins()`'s CLVM execution the same way the rest of `Offer` does: replace `cost_left = INFINITE_COST` with a hard ceiling (e.g., `int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)`, mirroring `__post_init__` and `conditions()`), and raise a `ValidationError` (rather than asserting) when a spend's cost exceeds the remaining budget, so a single malicious inner puzzle cannot consume unbounded memory/CPU during offer inspection.

### Proof of Concept
1. Construct a `WalletSpendBundle` containing one `CoinSpend` whose `puzzle_reveal` matches a known puzzle driver (e.g., a CAT/settlement puzzle recognized by `match_puzzle`).
2. Craft the inner puzzle/solution so that, when run, it generates an extremely large number of CLVM pairs/atoms (e.g., a puzzle that builds a huge nested list via recursion), well beyond what `MAX_BLOCK_COST_CLVM` would ever allow.
3. Wrap this spend bundle in an `Offer` object (`Offer(requested_payments, bundle, driver_dict)`); `__post_init__`'s bounded `compute_spend_hints_and_additions` call may still succeed if that particular check is bypassed or the cost is spent specifically inside the *inner* puzzle execution path exercised only by `_get_offered_coins()`.
4. Have the victim call `offer.summary()` or `offer.get_offered_amounts()` (as done automatically by `chia wallet get_offer`/`take_offer` CLI/RPC flows) on the received offer bytes.
5. Observe that `_get_offered_coins()` invokes `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)`, running the CLVM interpreter without any cost/memory bound, consuming excessive memory and potentially crashing the wallet process.

Note: I was not able to fully confirm within the available index whether `__post_init__`'s bounded cost check (`compute_spend_hints_and_additions`) would independently reject such a crafted bundle before `get_offered_coins()` is ever called — this depends on exact opcode-level behavior of `compute_spend_hints_and_additions` versus the ownership-layer/inner-puzzle path taken by `_get_offered_coins()`, which uses a different puzzle-matching route (`match_puzzle`/`get_inner_puzzle`/`get_inner_solution`). Verifying this precisely, and constructing a concrete crafted puzzle/solution, would require running the code in a live Devin session with full repository access.

### Citations

**File:** chia/wallet/trading/offer.py (L163-184)
```python
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

**File:** chia/wallet/trading/offer.py (L188-200)
```python
    def conditions(self) -> dict[Coin, list[Condition]]:
        if self._conditions is None:
            conditions: dict[Coin, list[Condition]] = {}
            max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
            for cs in self._bundle.coin_spends:
                try:
                    cost, conds = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)
                    max_cost -= cost
                    conditions[cs.coin] = parse_conditions_non_consensus(conds.as_iter())
                except Exception:  # pragma: no cover
                    continue
                if max_cost < 0:  # pragma: no cover
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "computing conditions for CoinSpend")
```

**File:** chia/wallet/trading/offer.py (L244-265)
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
```

**File:** chia/wallet/trading/offer.py (L305-311)
```python
    def get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        try:
            if self._offered_coins is not None:
                return self._offered_coins
        except AttributeError:
            object.__setattr__(self, "_offered_coins", self._get_offered_coins())
        return self._offered_coins
```

**File:** chia/wallet/trading/offer.py (L313-367)
```python
    def get_offered_amounts(self) -> dict[bytes32 | None, int]:
        offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        offered_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in offered_coins.items():
            offered_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return offered_amounts

    def get_requested_payments(self) -> dict[bytes32 | None, list[NotarizedPayment]]:
        return self.requested_payments

    def get_requested_amounts(self) -> dict[bytes32 | None, int]:
        requested_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in self.get_requested_payments().items():
            requested_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return requested_amounts

    def arbitrage(self) -> dict[bytes32 | None, int]:
        """
        Returns a dictionary of the type of each asset and amount that is involved in the trade
        With the amount being how much their offered amount within the offer
        exceeds/falls short of their requested amount.
        """
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        arbitrage_dict: dict[bytes32 | None, int] = {}
        for asset_id in [*requested_amounts.keys(), *offered_amounts.keys()]:
            arbitrage_dict[asset_id] = offered_amounts.get(asset_id, 0) - requested_amounts.get(asset_id, 0)

        return arbitrage_dict

    # This is a method mostly for the UI that creates a JSON summary of the offer
    def summary(self) -> tuple[dict[str, str], dict[str, str], dict[str, PuzzleInfo], ConditionValidTimes]:
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        def keys_and_amounts_to_strings(dic: dict[bytes32 | None, int]) -> dict[str, str]:
            new_dic: dict[str, str] = {}
            for key, val in dic.items():
                if key is None:
                    new_dic["xch"] = str(val)
                else:
                    new_dic[key.hex()] = str(val)
            return new_dic

        driver_dict: dict[str, PuzzleInfo] = {}
        for key, value in self.driver_dict.items():
            driver_dict[key.hex()] = value

        return (
            keys_and_amounts_to_strings(offered_amounts),
            keys_and_amounts_to_strings(requested_amounts),
            driver_dict,
            self.absolute_valid_times_ban_relatives(),
        )
```

**File:** chia/wallet/trading/offer.py (L371-393)
```python
    def get_pending_amounts(self) -> dict[str, int]:
        all_additions: list[Coin] = self.additions()
        all_removals: list[Coin] = self.removals()
        non_ephemeral_removals: list[Coin] = list(filter(lambda c: c not in all_additions, all_removals))

        pending_dict: dict[str, int] = {}
        # First we add up the amounts of all coins that share an ancestor with the offered coins (i.e. a primary coin)
        for asset_id, coins in self.get_offered_coins().items():
            name = "xch" if asset_id is None else asset_id.hex()
            pending_dict[name] = 0
            for coin in coins:
                root_removal: Coin = self.get_root_removal(coin)

                for addition in filter(lambda c: c.parent_coin_info == root_removal.name(), all_additions):
                    pending_dict[name] += addition.amount

        # Then we gather anything else as unknown
        sum_of_additions_so_far: int = sum(pending_dict.values())
        unknown: int = sum(c.amount for c in non_ephemeral_removals) - sum_of_additions_so_far
        if unknown > 0:
            pending_dict["unknown"] = unknown

        return pending_dict
```

**File:** chia/full_node/mempool_manager.py (L550-575)
```python
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
```
