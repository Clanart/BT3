### Title
Unbounded CLVM execution when parsing untrusted offer files enables a griefing DoS on wallets/trade managers - ([File: chia/wallet/trading/offer.py])

### Finding Description
The external report's root cause is that `unlockFor` runs an unbounded operation (return-data copy for an externally-supplied payload) with no per-call cost/size cap, letting a malicious counterparty force the caller (a relayer) to pay excessive resources.

The same structural pattern exists in `Offer._get_offered_coins()`. This method is invoked when a wallet (or `TradeManager`) inspects an **untrusted offer file received from an offer counterparty**, in order to determine what is being offered. For every coin spend contained in the offer, it runs the inner puzzle with:

```python
cost_left = INFINITE_COST
...
puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
assert cost_left >= puzzle_cost
cost_left -= puzzle_cost
``` [1](#0-0) 

`cost_left` is initialized to `INFINITE_COST` and is never bounded to a sane per-offer or per-spend limit before running attacker-supplied CLVM (`inner_puzzle`/`inner_solution` come directly from the offer's `coin_spends`, which are fully attacker-controlled since the offer has not yet been accepted/pushed to the mempool and thus has not gone through mempool cost admission). This mirrors the reported bug class: an untrusted payload is processed without a bounded cost limit before the caller decides whether to accept it, letting the attacker force expensive execution on the victim's node/wallet.

### Impact Explanation
A malicious offer-creator can craft an offer file whose coin spends contain a puzzle/solution designed to consume very large CLVM execution cost or memory (similar to the "large_string"/exponential-growth CLVM patterns already used elsewhere in the codebase's own malicious-generator mempool tests, e.g. `SINGLE_ARG_INT_COND` in `chia/_tests/core/mempool/test_mempool.py:2620-2649`) [2](#0-1) . When a victim wallet loads/inspects that offer (e.g. via `TradeManager`/RPC `get_offer_summary`-style flows that call into `Offer.get_offered_coins()`/`_get_offered_coins()`), the unbounded `run_with_cost(INFINITE_COST, ...)` call can consume disproportionate CPU/memory before any admission or fee is paid, potentially stalling or crashing the wallet process that is evaluating the offer. This is a griefing/DoS vector against any wallet user or Data Layer client that inspects offers received from counterparties, reachable without any economic cost to the attacker beyond crafting the offer file.

### Likelihood Explanation
Likelihood is moderate: offer files are commonly exchanged and inspected out-of-band (via CLI/RPC `get_offer_summary`, `examine offer`, or `TradeManager` calling `get_offered_coins`) before a user decides to accept, and any counterparty can construct a spend with an arbitrarily expensive puzzle since there is no on-chain cost admission for an unaccepted offer. The victim's wallet is expected to safely display/summarize offers without paying attention to malicious cost, which the current code does not enforce.

### Recommendation
Bound the CLVM execution cost used when inspecting offer coin spends. Replace `cost_left = INFINITE_COST` in `Offer._get_offered_coins()` with an explicit, reasonable maximum cost (e.g. `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` or a smaller sanity limit), and reject/error out offers whose spends exceed that limit before further processing, similar to how `compute_additions_with_cost()` and `compute_spend_hints_and_additions()` already bound cost with `max_cost` parameters (`chia/wallet/util/compute_additions.py`, `chia/wallet/util/compute_hints.py`).

### Proof of Concept
1. Craft an offer whose `coin_spends` include a puzzle reveal similar in structure to the malicious mempool test generators (`SINGLE_ARG_INT_COND` / large_string doubling pattern) that is cheap to construct but extremely expensive to execute.
2. Serialize this as a standard offer file and send it to a victim (e.g. via chat/file exchange), as is standard offer workflow.
3. Victim's wallet calls `Offer.from_bytes(...)` then `get_offered_coins()`/`_get_offered_coins()` (e.g. through `TradeManager.get_offer_summary` or equivalent RPC/CLI inspection path) which invokes `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)` with no cap.
4. The victim's wallet process consumes excessive CPU/memory time attempting to execute the puzzle, before the user has decided whether to accept the offer, causing a denial-of-service on the wallet. [3](#0-2)

### Citations

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

**File:** chia/_tests/core/mempool/test_mempool.py (L2620-2634)
```python
# the tests below are malicious generator programs

# this program:
# (mod (A B)
#  (defun large_string (V N)
#    (if N (large_string (concat V V) (- N 1)) V)
#  )
#  (defun iter (V N)
#    (if N (c V (iter V (- N 1))) ())
#  )
#  (iter (c (q . 83) (c (concat (large_string 0x00 A) (q . 100)) ())) B)
# )
# with A=28 and B specified as {num}

SINGLE_ARG_INT_COND = "(a (q 2 4 (c 2 (c (c (q . {opcode}) (c (concat (a 6 (c 2 (c (q . {filler}) (c 5 ())))) (q . {val})) ())) (c 11 ())))) (c (q (a (i 11 (q 4 5 (a 4 (c 2 (c 5 (c (- 11 (q . 1)) ()))))) ()) 1) 2 (i 11 (q 2 6 (c 2 (c (concat 5 5) (c (- 11 (q . 1)) ())))) (q . 5)) 1) (q 28 {num})))"  # ruff: ignore[line-too-long]
```
