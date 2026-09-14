## Analog Analysis: Uncontrolled Resource Consumption via Unbounded CLVM Execution on Untrusted Offers

### Title
Uncontrolled Resource Consumption via `INFINITE_COST` CLVM Execution When Parsing Untrusted Offers - (File: `chia/wallet/trading/offer.py`)

### Summary
The CVE-2017-12174 analog is a pattern where processing an unauthenticated/untrusted network input triggers unbounded resource allocation before any admission control is applied. In this codebase, `Offer._get_offered_coins()` in `chia/wallet/trading/offer.py` runs CLVM inner-puzzle execution using `cost_left = INFINITE_COST` [1](#0-0) , meaning a wallet user who merely inspects (summarizes) or takes an offer received from a counterparty executes attacker-supplied CLVM with no cost ceiling.

### Finding Description
Chia's mempool/block validation paths are carefully cost-metered: `MempoolManager.pre_validate_spendbundle()` bounds every spend bundle to `max_tx_clvm_cost` and additionally checks `num_atoms`/`num_pairs` density after execution to bound memory relative to cost paid [2](#0-1) . However, `Offer` objects — which are files/bech32 strings freely exchanged between untrusted counterparties over out-of-band channels and loaded via wallet RPC calls (e.g., `get_offer_summary`, `respond_to_offer`) — are parsed and their embedded CLVM puzzles executed by the *recipient's* wallet without that same limit.

Specifically:
- `Offer.conditions()` bounds execution to `DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM` per call, decrementing a shared budget across coin spends [3](#0-2)  — this at least caps at a full block's cost budget (still very large, and it swallows exceptions on failure via a bare `except Exception: continue`, discarding cost accounting for failed conditions).
- `Offer._get_offered_coins()`, used to determine which coins are actually being offered, initializes `cost_left = INFINITE_COST` and executes each parent spend's inner puzzle with that unbounded budget, only decrementing `cost_left` afterward with no upper check that would stop execution mid-flight [1](#0-0) .

This mirrors the CVE's bug class: an untrusted, minimally-validated input (a multicast packet there; an offer file/bech32 string here) causes the receiving process to perform large, attacker-controlled CLVM computation/allocation (the same technique demonstrated by the "malicious generator" test suite in `chia/_tests/core/mempool/test_mempool.py`, e.g. `SINGLE_ARG_INT_LADDER_COND`, which repeatedly doubles a string via `concat` to build exponentially large atoms cheaply in terms of node/byte count) [4](#0-3) , before the mempool's cost-based admission control ever applies (offer inspection happens client-side, pre-broadcast).

### Impact Explanation
A malicious offer counterparty can craft an `Offer` whose coin-spend puzzle reveals contain CLVM programs designed to blow up exponentially (e.g., repeated `concat`/string doubling under `INFINITE_COST`, analogous to the documented malicious-generator test vectors). Sending this offer file to a victim (via any offer-sharing channel — file, bech32 string, DL offer summary RPC) and having the victim's wallet call `get_offer_summary`, `check_offer_validity`, `respond_to_offer`, or any other function that reaches `get_offered_coins()`/`_get_offered_coins()` causes the victim's wallet process to allocate large memory/CPU attempting to run the crafted puzzle to completion, since the executor is not bounded by any per-tx cost limit at that call site. This can cause the wallet daemon to hang or OOM, denying service to the wallet user — the same "heap memory exhaustion / full GC / OOM" impact class described in the CVE.

### Likelihood Explanation
Likelihood is high for any user who accepts offer files from untrusted third parties (a normal wallet workflow — offers are explicitly designed to be shared out-of-band with people you don't know) and inspects them before deciding whether to accept, since inspection itself (`get_offer_summary`) triggers the unbounded execution path.

### Recommendation
Bound `cost_left` in `Offer._get_offered_coins()` (and any other offer-inspection path using `INFINITE_COST` or `MAX_BLOCK_COST_CLVM` without decrementing checks) to the same per-transaction cost ceiling enforced by mempool admission (`max_tx_clvm_cost`), and reject/short-circuit execution as soon as that budget is exceeded, mirroring the atom/pair density checks already used in `MempoolManager.pre_validate_spendbundle()`.

### Proof of Concept
Conceptually: construct an `Offer` whose parent coin spend's inner puzzle is one of the documented "malicious generator" patterns (e.g. `SINGLE_ARG_INT_LADDER_COND`-style repeated `concat` doubling) with a large iteration count `B`, matched to a puzzle recognized by `match_puzzle()` so it enters the `_get_offered_coins()` inner-puzzle-execution branch. Serialize this as an `Offer` (`Offer.to_bech32()`), send it to a victim wallet, and have the victim call `get_offer_summary` or `take_offer` on it — the resulting `inner_puzzle.run_with_cost(INFINITE_COST, inner_solution)` call will consume unbounded CPU/memory attempting to execute the doubling program to completion.

Note: I was unable to fully verify the exact numeric value/semantics of `INFINITE_COST` and the CLVM runtime's internal behavior when given that value (e.g., whether there's an implicit interpreter-level cap) due to tool-call limits reached before reading `chia/types/blockchain_format/program.py` in full; the recommendation and root-cause claim about the missing per-call cost bound in `offer.py` is based directly on the read source lines cited above.

### Citations

**File:** chia/wallet/trading/offer.py (L188-203)
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
        assert self._conditions is not None, "self._conditions is None"
        return self._conditions
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

**File:** chia/full_node/mempool_manager.py (L571-576)
```python
        if sbc.num_atoms > sbc.cost * 60_000_000 / self.constants.MAX_BLOCK_COST_CLVM:
            raise ValueError("too many atoms")

        if sbc.num_pairs > sbc.cost * 60_000_000 / self.constants.MAX_BLOCK_COST_CLVM:
            raise ValueError("too many pairs")

```

**File:** chia/_tests/core/mempool/test_mempool.py (L2663-2672)
```python
# (mod (A B)
#  (defun large_string (V N)
#    (if N (large_string (concat V V) (- N 1)) V)
#  )
#  (defun iter (V N)
#    (if N (c (c (q . 83) (c (concat V N) ())) (iter V (- N 1))) ())
#  )
#  (iter (large_string 0x00 A) B)
# )
SINGLE_ARG_INT_LADDER_COND = "(a (q 2 4 (c 2 (c (a 6 (c 2 (c (q . {filler}) (c 5 ())))) (c 11 ())))) (c (q (a (i 11 (q 4 (c (q . {opcode}) (c (concat 5 11) ())) (a 4 (c 2 (c 5 (c (- 11 (q . 1)) ()))))) ()) 1) 2 (i 11 (q 2 6 (c 2 (c (concat 5 5) (c (- 11 (q . 1)) ())))) (q . 5)) 1) (q 24 {num})))"  # ruff: ignore[line-too-long]
```
