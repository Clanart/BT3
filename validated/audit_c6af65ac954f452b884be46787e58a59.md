### Title
Unguarded `assert` in `Offer._get_offered_coins()` lets a malicious offer counterparty crash wallet offer processing - (File: chia/wallet/trading/offer.py)

### Summary
`Offer._get_offered_coins()` in `chia/wallet/trading/offer.py` contains `assert inner_puzzle is not None and inner_solution is not None` right after calling `get_inner_puzzle()`/`get_inner_solution()` on a puzzle that `match_puzzle()` recognized as a known outer-puzzle driver (CAT, NFT, VC/CR-CAT, etc.). [1](#0-0)  If a crafted offer's coin spend has a puzzle reveal that matches one of the known outer puzzle types but carries a solution shape that causes the corresponding driver's `get_inner_puzzle`/`get_inner_solution` to return `None`, this bare `assert` fires and raises an uncaught `AssertionError` instead of a handled error. This mirrors the Ella Core NGAP handover-failure bug class (CWE-476): a peer-controlled, non-happy-path input reaches processing code that assumes a value is always populated and panics/crashes when that assumption is violated.

### Finding Description
`_get_offered_coins()` is the core routine that walks every coin spend in an untrusted `Offer`/`WalletSpendBundle`, matches its puzzle reveal against known outer-puzzle drivers via `match_puzzle()`, and then unwraps it to find the inner puzzle/solution in order to compute what coins the offer maker is actually offering:

```python
puzzle_driver = match_puzzle(parent_puzzle)
if puzzle_driver is not None:
    asset_id = create_asset_id(puzzle_driver)
    inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
    inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
    assert inner_puzzle is not None and inner_solution is not None
``` [1](#0-0) 

`get_inner_puzzle`/`get_inner_solution` are dispatched through `chia/wallet/outer_puzzles.py` to per-asset-type drivers (CAT, NFT ownership/metadata/singleton layers, VC/CR-CAT credential-restriction layer, etc.). Each of these drivers independently parses the solution structure (e.g. via `uncurry()`/`Program.at(...)`) to extract the inner layer; a solution that is syntactically acceptable to `match_puzzle` (which typically only inspects the puzzle's curried mod structure, not the solution) but structurally malformed relative to what a specific driver expects can legitimately cause one of these accessor functions to return `None` rather than raising a caught exception, because the code path assumes solutions always have the expected canonical shape.

Unlike the sibling code in `Offer.__post_init__`, which wraps its own puzzle-parsing (`compute_spend_hints_and_additions`) in a broad `try/except Exception: continue`, [2](#0-1)  `_get_offered_coins()` has no such guard around the `assert`. Because `AssertionError` is not caught by any `try/except` in this call chain, it propagates up through `get_offered_coins()`, `arbitrage()`/`get_offered_amounts()`, and into any caller that inspects an untrusted offer.

### Impact Explanation
This function is on the reachable path for any wallet user who loads/examines/responds to an offer received from a counterparty:
- `TradeManager.respond_to_offer()` (backing the `take_offer` RPC used when a user accepts an offer) calls `offer.arbitrage()` early in processing, before any of the offer's coins are actually spent. [3](#0-2) 
- `WalletRpcApi.take_offer()` forwards directly to `respond_to_offer()`. [4](#0-3) 
- CLI `take_offer` calls `offer.summary()` (which relies on the same offered-coins computation) to display the offer before the user is even asked to confirm. [5](#0-4) 

A malicious offer counterparty can hand a wallet operator a crafted offer file/bech32 blob whose CAT/NFT/VC puzzle reveal matches a known driver but whose solution is shaped to make `get_inner_puzzle`/`get_inner_solution` return `None`. Simply examining or attempting to take that offer triggers the unguarded `assert`, raising an unhandled `AssertionError` in the RPC/CLI call path — a spend-triggered halt of wallet transaction processing for that request, analogous to how a crafted NGAP handover-failure message panics Ella Core. This is a service-disruption (availability) issue reachable by an unprivileged offer counterparty, not an asset-forgery or fund-theft bug.

### Likelihood Explanation
Medium. Exploitation requires only sending or publishing a crafted offer file — no privileged access or on-chain broadcast is needed, since the crash occurs purely from local processing of the untrusted offer bytes (summary/examine/take-offer path) before any spend is submitted to the mempool. The attacker needs enough knowledge of the specific outer-puzzle driver's solution parsing to make `get_inner_puzzle`/`get_inner_solution` return `None` while still passing `match_puzzle`, which requires some puzzle/solution crafting effort but is plausible given the puzzle formats are public.

### Recommendation
Replace the bare `assert inner_puzzle is not None and inner_solution is not None` in `_get_offered_coins()` with an explicit, handled error path (e.g., raise a caught `ValueError`/`ValidationError`, or `continue`/skip that spend similar to the `__post_init__` handling), so malformed but externally-supplied offers are rejected gracefully instead of crashing the calling RPC/CLI flow with an unguarded `AssertionError`. Audit other outer-puzzle driver call sites in `offer.py` for similar unguarded asserts on `None`-returning accessor functions fed by untrusted solutions.

### Proof of Concept
Conceptual (not fully executed/validated in this analysis due to the complexity of crafting a driver-specific malformed solution):
1. Construct an `Offer` whose coin spend's puzzle reveal matches a known outer puzzle (e.g. CAT or NFT ownership layer) via `match_puzzle`.
2. Craft the solution for that coin spend so that the specific driver's `get_inner_puzzle`/`get_inner_solution` implementation (in `chia/wallet/outer_puzzles.py` and the corresponding `*_outer_puzzle.py`) fails to locate the expected inner-layer structure and returns `None`, while `match_puzzle` still succeeds because it only inspects the outer curry structure.
3. Serialize this into an offer bech32 string and hand it to a victim wallet.
4. Victim calls `take_offer` (CLI or RPC) or otherwise triggers `offer.summary()`/`arbitrage()` on the received offer.
5. `Offer._get_offered_coins()` hits `assert inner_puzzle is not None and inner_solution is not None`, raising an uncaught `AssertionError` that surfaces as an unhandled exception in the wallet RPC/CLI call, rather than a clean rejection of the malformed offer.

Note: I was not able to fully construct and verify a concrete failing puzzle/solution pair for a specific driver (e.g., CAT vs. NFT vs. CR-CAT) within this analysis; confirming exploitability end-to-end would require exercising `get_inner_puzzle`/`get_inner_solution` for each driver in `chia/wallet/*_outer_puzzle.py` against adversarial solutions to find one that returns `None` without match_puzzle also rejecting it.

### Citations

**File:** chia/wallet/trading/offer.py (L169-181)
```python
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
```

**File:** chia/wallet/trading/offer.py (L255-264)
```python
            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None

                # We're going to look at the conditions created by the inner puzzle
                puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
                assert cost_left >= puzzle_cost
```

**File:** chia/wallet/trade_manager.py (L833-839)
```python
        take_offer_dict: dict[bytes32 | int, int] = {}
        arbitrage: dict[bytes32 | None, int] = offer.arbitrage()

        for asset_id, amount in arbitrage.items():
            if asset_id is None:
                wallet: WalletProtocol | None = self.wallet_state_manager.main_wallet
                assert wallet is not None
```

**File:** chia/wallet/wallet_rpc_api.py (L2050-2064)
```python
    async def take_offer(
        self,
        request: TakeOffer,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> TakeOfferResponse:
        peer = self.service.get_full_node_peer()
        trade_record = await self.service.wallet_state_manager.trade_manager.respond_to_offer(
            request.parsed_offer,
            peer,
            action_scope,
            fee=request.fee,
            solver=request.solver,
            extra_conditions=extra_conditions,
        )
```

**File:** chia/cmds/wallet_funcs.py (L833-840)
```python
    offered, requested, _, _ = offer.summary()
    cat_name_resolver = wallet_client.cat_asset_id_to_name
    network_xch = AddressType.XCH.hrp(config).upper()
    print("Summary:")
    print("  OFFERED:")
    await print_offer_summary(cat_name_resolver, offered, network_xch=network_xch)
    print("  REQUESTED:")
    await print_offer_summary(cat_name_resolver, requested, network_xch=network_xch)
```
