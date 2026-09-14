### Title
Reachable assertion in `compute_spend_hints_and_additions` via crafted CREATE_COIN puzzle-hash arg crashes offer-taking flow - ([File: chia/wallet/util/compute_hints.py])

### Summary
`compute_spend_hints_and_additions` parses CLVM `CREATE_COIN` conditions produced by running a coin spend's puzzle reveal, and unconditionally asserts that the puzzle-hash argument is an atom. If a malicious offer/coin-spend produces a `CREATE_COIN` condition whose second argument is a cons pair (list) instead of an atom, this raises a bare, unhandled `AssertionError`, analogous to CVE‑2024‑24420's reachable assertion in Magma's `decode_linked_ti_ie`, which crashed processing on malformed input.

### Finding Description
`compute_spend_hints_and_additions` runs the puzzle reveal/solution of a `CoinSpend` and, for every `CREATE_COIN` condition found in the CLVM output, does: [1](#0-0) 

```
if op != ConditionOpcode.CREATE_COIN.value:
    continue
cost += ConditionCost.CREATE_COIN.value

rf = condition.at("rf").atom
assert rf is not None

coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
```

`condition.at("rf")` retrieves the second element of the `(51 puzzle_hash amount ...)` condition list. If the puzzle reveal (fully attacker-controlled inside an Offer/spend bundle) emits `(51 (a . b) 100)` — i.e., a cons pair instead of an atom for the puzzle-hash slot — `.atom` evaluates to `None`, and `assert rf is not None` raises a raw `AssertionError`. This is not wrapped in `ValidationError`/`ConsensusError` the way the rest of the condition-parsing code (`chia/consensus/condition_tools.py`) is.

This function is called directly, without exception handling, from `TradeManager.calculate_tx_records_for_offer`: [2](#0-1) 

which is invoked from `TradeManager.respond_to_offer` when a wallet user accepts/takes an offer supplied by a counterparty: [3](#0-2) 

By contrast, the sibling call site in `Offer.__post_init__` wraps the same call in a broad `try/except Exception: continue` block, masking the crash there: [4](#0-3) 

but `calculate_tx_records_for_offer`'s direct generator-expression call at line 698 has no such protection, so the `AssertionError` propagates uncaught.

### Impact Explanation
An offer counterparty can craft an `Offer` (spend bundle) containing a coin spend whose puzzle emits a malformed `CREATE_COIN` condition (puzzle-hash argument as a pair rather than an atom). When the wallet owner calls `respond_to_offer`/take-offer on this crafted offer, the wallet-side transaction-processing task raises an unhandled `AssertionError` inside `calculate_tx_records_for_offer`, halting that operation. This is a spend/offer-triggered denial of service against the wallet's offer-acceptance flow, reachable purely by supplying an untrusted offer file — no privileged access, malicious peer, or network-layer capability required, matching the "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
Likelihood is high for any wallet user who takes/inspects a maliciously crafted offer: constructing a puzzle that outputs a `CREATE_COIN` condition with a non-atom puzzle-hash argument is trivial in CLVM (e.g., `(list 51 (c 1 2) 100)`), and the vulnerable code path (`calculate_tx_records_for_offer` → `compute_spend_hints_and_additions`) executes unconditionally when `validate=True` is passed, which is the case in `respond_to_offer`.

### Recommendation
Replace the bare `assert rf is not None` in `compute_spend_hints_and_additions` (chia/wallet/util/compute_hints.py:52-53) with an explicit check that raises `ValidationError`/`ConsensusError` (consistent with `Err.INVALID_CONDITION` handling elsewhere), and ensure `calculate_tx_records_for_offer` in `chia/wallet/trade_manager.py` either relies on this graceful error or wraps the call in appropriate exception handling so malformed offers are rejected rather than crashing the wallet task.

### Proof of Concept
1. Construct a `CoinSpend` whose puzzle reveal, when run, outputs the condition list `((51 (99 . 100) 1000))` — i.e., `CREATE_COIN` with a cons pair `(99 . 100)` instead of a 32-byte atom as the puzzle-hash argument (e.g. puzzle `(mod () (list (list 51 (c 99 100) 1000)))`).
2. Package this spend into an `Offer`/spend bundle and have the victim wallet call `TradeManager.respond_to_offer` (i.e., take the offer) or otherwise trigger `calculate_tx_records_for_offer(offer, validate=True)`.
3. Observe `compute_spend_hints_and_additions` execute `assert rf is not None` with `rf = None`, raising an unhandled `AssertionError` that propagates out of `calculate_tx_records_for_offer`, halting the offer-take operation.

Note: I was unable to fully trace which specific wallet RPC endpoint invokes `respond_to_offer` (the `wallet_rpc_api.py` grep for `respond_to_offer`/`take_offer` returned no direct matches, likely due to indexing limits or different naming in the RPC layer), so the exact external trigger surface (RPC method name) could not be confirmed from the indexed content. I'd recommend starting a full Devin session to inspect `chia/wallet/wallet_rpc_api.py` directly for the take-offer RPC handler to confirm the exact external call chain.

### Citations

**File:** chia/wallet/util/compute_hints.py (L48-56)
```python
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value

        rf = condition.at("rf").atom
        assert rf is not None

        coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
        hint: bytes32 | None = None
```

**File:** chia/wallet/trade_manager.py (L692-701)
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
```

**File:** chia/wallet/trade_manager.py (L887-894)
```python
        self.log.info("COMPLETE OFFER: %s", complete_offer.to_bech32())
        assert complete_offer.is_valid()
        final_spend_bundle: WalletSpendBundle = complete_offer.to_valid_spend(
            solver=Solver({**valid_spend_solver.info, **solver.info})
        )
        await self.maybe_create_wallets_for_offer(complete_offer)

        tx_records: list[TransactionRecord] = await self.calculate_tx_records_for_offer(complete_offer, True)
```

**File:** chia/wallet/trading/offer.py (L166-184)
```python
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
