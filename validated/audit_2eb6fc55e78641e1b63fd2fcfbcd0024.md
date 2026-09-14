### Title
Unbounded CLVM execution when parsing an untrusted offer's inner puzzles causes wallet DoS - (File: chia/wallet/trading/offer.py)

### Summary
`Offer._get_offered_coins()` in `chia/wallet/trading/offer.py` executes the inner puzzle of every coin spend in a received (potentially malicious) offer using `cost_left = INFINITE_COST`, bypassing the cost budgeting that every other offer-processing path in the same file enforces (`__post_init__` and `conditions()` both track a finite `max_cost = DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` budget). This mirrors the Xen CVE-2018-12891 bug class: most code paths correctly enforce a bound/preemption check, but a specific, less-obvious path bypasses it, letting an attacker-supplied input run for an unbounded amount of time/resources.

### Finding Description
`Offer.__post_init__()` and `Offer.conditions()` both correctly decrement a finite `max_cost` (`DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`) while iterating and executing each `CoinSpend`'s puzzle, raising `ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, ...)` if the budget is exceeded: [1](#0-0) [2](#0-1) 

However, `_get_offered_coins()` — used to determine which coins in an offer are the actual "offered" assets — initializes `cost_left = INFINITE_COST` and runs each parent spend's *inner puzzle* with that unlimited budget: [3](#0-2) 

Because `INFINITE_COST` removes the CLVM cost ceiling entirely for this specific code path, a malicious offer counterparty can craft a `CoinSpend` whose puzzle (matched by `match_puzzle`/`get_inner_puzzle`, e.g. a CAT/NFT/singleton driver) contains a computationally explosive CLVM program (e.g., deeply recursive doubling/exponential expansion) in its inner puzzle. When the wallet inspects/validates the offer (a normal, expected step before accepting/pushing a trade), this single unmetered `run_with_cost` call can consume unbounded CPU time and memory on the wallet host — the local analog of Xen's "long-running MMU operation bypassing preemption checks," where cost/time metering exists broadly in the system but is specifically skipped in this rarer path.

This is reachable purely from parsing an untrusted `Offer` object (e.g., a `.offer` file or bech32m string received from a counterparty), which is the exact kind of "offer counterparty" input surface explicitly in scope.

### Impact Explanation
A malicious offer file/string can hang or exhaust resources on any wallet process that inspects it (e.g., displaying an offer summary, computing arbitrage, or preparing to accept it), without requiring the victim to ever sign or broadcast anything. This is a spend-triggered (offer-triggered) transaction-processing halt / local DoS against the wallet component, consistent with the "spend-triggered transaction-processing halt" impact category explicitly accepted by the validation rules. It does not directly cause fund loss, forged assets, or invalid on-chain acceptance — the impact is confined to CPU/time exhaustion in a single wallet process, so it is bounded to Medium severity, matching the CVSS 6.5 / Medium rating of the underlying Xen advisory (local, low-complexity resource exhaustion, no confidentiality/integrity impact).

### Likelihood Explanation
Likelihood is fairly high given the design: `_get_offered_coins()` is invoked automatically whenever an `Offer` is constructed/inspected in normal flows such as computing which coins are offered before validating or accepting a trade, and offers are routinely exchanged between mutually distrusting parties (that is the entire threat model of the offer protocol). No signature or special privilege is needed — merely constructing an `Offer` from attacker-supplied bytes is enough to trigger the unmetered puzzle execution.

### Recommendation
Replace `cost_left = INFINITE_COST` in `_get_offered_coins()` with the same bounded budget used elsewhere in the file (`DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`), decrementing and rejecting with `ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, ...)` exactly as `__post_init__`/`conditions()` do, so that every CLVM execution path triggered by untrusted offer data is subject to the same cost ceiling and cannot run unbounded.

### Proof of Concept
1. Construct a `CoinSpend` whose `puzzle_reveal` uncurries to a known outer puzzle (so `match_puzzle` succeeds, e.g. CAT) wrapping an inner puzzle designed to be extremely expensive to run (e.g., a CLVM program that repeatedly doubles a large value via nested `(a (q ...) ...)` recursion many times, similar in spirit to the "large_string"/"iter" doubling patterns already used for cost-exhaustion tests elsewhere in the codebase, e.g. `SINGLE_ARG_INT_LADDER_COND` in `chia/_tests/core/mempool/test_mempool.py:2672`).
2. Package this `CoinSpend` (with matching `additions`) into a `WalletSpendBundle` and wrap it as an `Offer(requested_payments={}, bundle, driver_dict)`.
3. Have a victim wallet load/parse this offer (e.g., via `Offer.from_bech32`/`from_bytes` followed by any call path that invokes `arbitrage()`/`get_offered_coins()`, which internally calls `_get_offered_coins()`).
4. Observe that `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)` at `offer.py:263` runs without any cost bound, unlike the properly-bounded `__post_init__`/`conditions()` paths, consuming CPU for a duration proportional to the attacker-chosen exponential program rather than being rejected early with `BLOCK_COST_EXCEEDS_MAX`. [3](#0-2)

### Citations

**File:** chia/wallet/trading/offer.py (L165-184)
```python
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

**File:** chia/wallet/trading/offer.py (L188-201)
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
            object.__setattr__(self, "_conditions", conditions)
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
