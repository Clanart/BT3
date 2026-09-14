### Title
Silent swallowing of exceptions in `Offer.__post_init__` hides real coin creations from offer summaries/validation - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.__post_init__` builds the `_additions`/`_hints` cache that backs `Offer.additions()`, `Offer.hints()`, and (via `_get_offered_coins`) `Offer.get_offered_coins()`/`get_offered_amounts()`, which are used to compute and display what an offer actually offers/requests before a taker signs off on it. This mirrors the `Distributor._claim` `onClaim` bug class: a per-item `try`/`except` around an external, attacker-influenced computation is used to isolate failures, but it is implemented so that a genuine failure in one coin spend's condition parsing is silently swallowed (`except Exception: continue`) rather than surfaced, corrupting the aggregate result that downstream trust decisions rely on.

### Finding Description
In `Offer.__post_init__`, for every `CoinSpend` in the offer bundle, `compute_spend_hints_and_additions` is run to compute that spend's `CREATE_COIN` outputs: [1](#0-0) 

Any exception other than `ValidationError` or the specific "cost exceeded" `ValueError` is caught by a bare `except Exception: continue`, meaning that particular coin spend's additions/hints are silently dropped from the `_additions`/`_hints` dict with no error surfaced, no log, and no rejection of the offer.

`compute_spend_hints_and_additions` itself performs unguarded `Program.at(...)`/`.atom` accesses on CLVM output that the *offer maker* fully controls (arbitrary puzzle reveal + solution): [2](#0-1) 

A maker can therefore craft a coin spend whose CLVM output, when parsed by this specific hint/addition-extraction routine, raises an exception (e.g., malformed `CREATE_COIN` condition structure that this parser's `.at("rf")`/`.at("rrf")` calls don't tolerate) even though the puzzle/solution itself executes successfully and legitimately creates a coin. Because the exception is swallowed, that real, on-chain coin creation is entirely absent from `Offer.additions()`.

This cached, possibly-incomplete `_additions` is then relied on elsewhere, e.g. `_get_offered_coins`, which determines what asset/coin the offer is actually offering by cross-referencing `self._additions[parent_spend.coin]`: [3](#0-2) 

and is exposed to RPC/CLI users as the offer's `additions`/`removals` summary shown before acceptance: [4](#0-3) 

Note that `TradeManager.calculate_tx_records_for_offer`, when `validate=True`, recomputes hints/additions directly via `compute_spend_hints_and_additions` rather than relying on the cache, so the swallowed-exception cache is primarily consumed by the summary/offered-coins path rather than the final validated spend path: [5](#0-4) 

### Impact Explanation
This is the same root cause class as the reported `Distributor` bug: an asymmetric, overly broad exception handler around processing of externally-supplied (here, maker-supplied) data causes genuine failures to be silently discarded instead of being treated as an error condition, corrupting a value (`_additions`/`_hints`) that downstream code and UI trust as authoritative. If a maker can trigger this swallowed-exception path selectively for one of several coin spends in an offer, the offer's advertised "additions"/"offered coins" summary can diverge from what will actually happen when the offer is completed on-chain, undermining the integrity guarantee that a taker's wallet display accurately reflects the trade before they sign. This falls under "offer settlement theft"-adjacent risk: a taker could be shown an incomplete/misleading picture of the coins involved in a trade they are about to accept.

### Likelihood Explanation
Reaching this code path requires only that a counterparty construct a custom offer file (fully permitted, unprivileged action for any offer counterparty) containing a coin spend whose puzzle/solution is designed so `compute_spend_hints_and_additions`'s specific parsing (`.at("rf")`, `.at("rrf")`, `.at("rrrff")`, etc.) throws for that spend while the spend still executes and creates a coin under normal CLVM execution semantics used elsewhere. Constructing such a discrepancy requires crafting a puzzle output that is valid enough to run without a `ValidationError`/cost-exceeded `ValueError` but structurally unexpected to this narrow parser (e.g., non-atom "puzzle hash" arg fields at the exact offsets this function reads) — this is plausible but requires careful engineering of the CLVM output, and I could not fully verify from the index whether the on-chain `CREATE_COIN` condition path (`chia_rs` consensus parsing) is lenient enough at exactly the same structural positions to accept such output while this Python helper crashes. Because full certainty about achieving the discrepancy would require deeper testing of `compute_spend_hints_and_additions` versus consensus condition parsing (not available via search alone), likelihood is best characterized as speculative/uncertain rather than confirmed.

### Recommendation
- Do not use a bare `except Exception: continue` in `Offer.__post_init__`; either propagate the exception (fail closed, rejecting the offer) or explicitly enumerate/handle only the specific, well-understood exception types that indicate a benign case (e.g., no `CREATE_COIN` conditions), and log/raise for anything else.
- Ensure `compute_spend_hints_and_additions`'s parsing logic is provably equivalent (or a strict superset) of consensus-level `CREATE_COIN` condition parsing so it cannot fail on inputs that are otherwise valid on-chain.
- Have `Offer.additions()`/`get_offered_coins()` fail loudly (raise) rather than silently omit data when any coin spend's additions cannot be computed, since these are used to inform trust/acceptance decisions.

### Proof of Concept
Not fully constructible from static analysis alone — verifying real exploitability requires crafting a concrete CLVM puzzle/solution pair that (a) executes successfully and produces a `CREATE_COIN` condition accepted by consensus condition parsing, while (b) causing `compute_spend_hints_and_additions`'s `.at("rf")`/`.at("rrf")`/`.at("rrrff")` traversal to raise a non-`ValidationError`/non-cost exception, and then confirming that the resulting `Offer.additions()`/summary omits that coin while the coin is still created when the offer is completed. This would need to be validated with a running Devin session that can execute CLVM and the wallet test harness (e.g., extending `chia/_tests/wallet/test_util.py::test_compute_spend_hints_and_additions` with a hand-crafted malformed-but-valid condition) rather than through static code reading alone.

### Citations

**File:** chia/wallet/trading/offer.py (L162-186)
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
        object.__setattr__(self, "_hints", hints)
        object.__setattr__(self, "_conditions", None)
```

**File:** chia/wallet/trading/offer.py (L244-303)
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
                offered_amounts: list[int] = []
                for condition in conditions.as_iter():
                    if condition.first() == 51 and condition.rest().first() == OFFER_MOD_HASH:
                        expected_num_matches += 1
                        offered_amounts.append(condition.rest().rest().first().as_int())

                # Start by filtering additions that match the amount
                matching_spend_additions = [a for a in additions if a.amount in offered_amounts]

                if len(matching_spend_additions) == expected_num_matches:
                    coins_for_this_spend.extend(matching_spend_additions)
                # We didn't quite get there so now lets narrow it down by puzzle hash
                else:
                    # If we narrowed down too much, we can't trust the amounts so start over with all additions
                    if len(matching_spend_additions) < expected_num_matches:
                        matching_spend_additions = additions
                    matching_spend_additions = [
                        a
                        for a in matching_spend_additions
                        if a.puzzle_hash == construct_puzzle(puzzle_driver, OFFER_MOD).get_tree_hash()
                    ]
                    if len(matching_spend_additions) == expected_num_matches:
                        coins_for_this_spend.extend(matching_spend_additions)
                    else:
                        raise ValueError("Could not properly guess offered coins from parent spend")
            else:
                # It's much easier if the asset is bare XCH
                asset_id = None
                coins_for_this_spend.extend([a for a in additions if a.puzzle_hash == OFFER_MOD_HASH])

            # We only care about unspent coins
            coins_for_this_spend = [c for c in coins_for_this_spend if c not in self._bundle.removals()]

            if coins_for_this_spend != []:
                offered_coins.setdefault(asset_id, [])
                offered_coins[asset_id].extend(coins_for_this_spend)
        return offered_coins
```

**File:** chia/wallet/util/compute_hints.py (L23-66)
```python
def compute_spend_hints_and_additions(
    cs: CoinSpend,
    *,
    max_cost: int = DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
) -> tuple[dict[bytes32, HintedCoin], int]:
    cost, result_program = run_with_cost(cs.puzzle_reveal, max_cost, cs.solution)

    hinted_coins: dict[bytes32, HintedCoin] = {}
    for condition in result_program.as_iter():
        if cost > max_cost:
            raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_spend_hints_and_additions() for CoinSpend")
        atoms = condition.as_iter()
        op = next(atoms).atom
        if op in {
            ConditionOpcode.AGG_SIG_PARENT,
            ConditionOpcode.AGG_SIG_PUZZLE,
            ConditionOpcode.AGG_SIG_AMOUNT,
            ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_AMOUNT,
            ConditionOpcode.AGG_SIG_PARENT_PUZZLE,
            ConditionOpcode.AGG_SIG_UNSAFE,
            ConditionOpcode.AGG_SIG_ME,
        }:
            cost += ConditionCost.AGG_SIG.value
            continue
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value

        rf = condition.at("rf").atom
        assert rf is not None

        coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
        hint: bytes32 | None = None
        if (
            condition.at("rrr") != Program.NIL  # There's more than two arguments
            and condition.at("rrrf").atom is None  # The 3rd argument is a cons
        ):
            potential_hint: bytes | None = condition.at("rrrff").atom
            if potential_hint is not None and len(potential_hint) == 32:
                hint = bytes32(potential_hint)
        hinted_coins[bytes32(coin.name())] = HintedCoin(coin, hint)

    return hinted_coins, cost
```

**File:** chia/_tests/wallet/rpc/test_wallet_rpc.py (L1770-1779)
```python
    assert offer_summary_response_advanced.summary == OfferSummary(
        offered={"xch": "5"},
        requested={cat_asset_id.hex(): "1"},
        infos={key.hex(): info for key, info in driver_dict.items()},
        fees=uint64(1),
        additions=[c.name() for c in offer.additions()],
        removals=[c.name() for c in offer.removals()],
        valid_times=ConditionValidTimesAbsolute(),
    )
    assert offer_summary_response_advanced.summary == offer_summary_response.summary
```

**File:** chia/wallet/trade_manager.py (L692-712)
```python
    async def calculate_tx_records_for_offer(self, offer: Offer, validate: bool) -> list[TransactionRecord]:
        if validate:
            final_spend_bundle: WalletSpendBundle = offer.to_valid_spend()
            hint_dict: dict[bytes32, bytes32] = {}
            additions_dict: dict[bytes32, Coin] = {}
            for hinted_coins, _ in (
                compute_spend_hints_and_additions(spend) for spend in final_spend_bundle.coin_spends
            ):
                hint_dict.update({id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None})
                additions_dict.update({id: hc.coin for id, hc in hinted_coins.items()})
            all_additions: list[Coin] = list(a for a in additions_dict.values())
        else:
            final_spend_bundle = offer._bundle
            hint_dict = offer.hints()
            all_additions = offer.additions()

        settlement_coins: list[Coin] = [c for coins in offer.get_offered_coins().values() for c in coins]
        settlement_coin_ids: list[bytes32] = [c.name() for c in settlement_coins]

        removals: list[Coin] = final_spend_bundle.removals()
        additions: list[Coin] = list(a for a in all_additions if a not in removals)
```
