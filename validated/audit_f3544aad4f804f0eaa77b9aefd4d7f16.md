### Title
Unhandled `AssertionError` on Malformed `CREATE_COIN` Condition Crashes Wallet Coin Processing - ([File: chia/wallet/util/compute_hints.py])

### Summary
`compute_spend_hints_and_additions()` blindly asserts that the puzzle-hash argument of a `CREATE_COIN` condition is a CLVM atom, without validating this first. A coin spend whose puzzle output emits a malformed `CREATE_COIN` condition (puzzle-hash position replaced with a cons pair instead of an atom) triggers an uncaught `AssertionError` wherever this function is invoked, mirroring the CVE-2017-15721 bug class of "malformed/attacker-controlled message causes an unchecked null/assert failure that crashes processing" rather than being rejected gracefully.

### Finding Description
`parse_conditions_non_consensus`/`compute_spend_hints_and_additions` run the puzzle+solution of a `CoinSpend` and iterate its output conditions to compute additions and hints. For `CREATE_COIN`, the code does: [1](#0-0) 
`condition.at("rf")` extracts the first argument after the opcode (the intended puzzle hash). If the puzzle emits a cons pair there instead of an atom, `.atom` returns `None`, and the bare `assert rf is not None` fails with `AssertionError`. Unlike the consensus condition parser (`chia/consensus/condition_tools.py`), which explicitly raises a caught `ConsensusError` on malformed input, this wallet-facing helper has no defensive check and relies on an `assert`, which is both disabled under `python -O` and, even when active, is a Python exception that must be explicitly caught by every caller to avoid propagating.

This function is invoked from multiple caller sites that process spend bundles supplied by external, unprivileged parties: `chia/wallet/trading/offer.py`, `chia/wallet/trade_manager.py` (e.g., `calculate_tx_records_for_offer`), `chia/wallet/cat_wallet/cat_wallet.py`, `chia/wallet/did_wallet/did_wallet.py`, `chia/wallet/vc_wallet/cr_cat_wallet.py`, and `chia/wallet/wallet_state_manager.py`. An attacker who crafts a coin spend (e.g., inside an offer or a spend bundle a wallet is asked to evaluate) whose puzzle emits `(CREATE_COIN (X . Y) amount)` — a cons pair instead of an atom for the puzzle hash — reaches the unguarded assert.

### Impact Explanation
If any of the callers listed above execute this path without wrapping it in a broad exception handler (verification incomplete — see caveat below, since the `wallet_state_manager.py` call sites and full body of `trade_manager.py`/`cat_wallet.py` could not be fully retrieved from the indexed context), the `AssertionError` propagates and halts wallet processing of that transaction/offer, a spend-triggered transaction-processing halt analogous to CVE-2017-15721's crash-on-malformed-input pattern. This is reachable by any wallet user accepting an offer or receiving a coin from an untrusted counterparty, without needing valid signatures for the malicious coin's own spend (only that its puzzle executes and returns this malformed condition).

### Likelihood Explanation
Likelihood is Medium: constructing a CLVM puzzle that outputs `(CREATE_COIN <cons> ...)` is trivial and requires no special privileges — any counterparty in an offer or any sender of a coin to a wallet can craft such a puzzle. The main open variable is whether every reachable call site actually lets the `AssertionError` propagate uncaught to the user-visible operation (offer parsing, sync, or coin-record apply), which could not be fully confirmed from the available indexed file contents.

### Recommendation
Replace the bare `assert rf is not None` in `compute_spend_hints_and_additions` (chia/wallet/util/compute_hints.py:52-53) with an explicit validation that raises a catchable, well-typed error (e.g., skip the malformed condition or raise `ValidationError`/`ValueError`) consistent with how `chia/consensus/condition_tools.py` handles malformed conditions. Additionally, audit each call site (`wallet_state_manager.py`, `trade_manager.py`, `cat_wallet.py`, `did_wallet.py`, `cr_cat_wallet.py`, `offer.py`) to ensure malformed-condition exceptions from any coin spend they process cannot crash a background sync task or transaction-processing loop.

### Proof of Concept
1. Craft a `CoinSpend` whose `puzzle_reveal` unconditionally returns `((51 (0x1111... . 0x2222...) 1000))` — i.e., a `CREATE_COIN` condition (opcode `51`) whose puzzle-hash argument is a cons pair rather than a 32-byte atom.
2. Include this coin spend inside an `Offer`/`SpendBundle` and have a wallet user call any path that calls `compute_spend_hints_and_additions` on it (e.g., `TradeManager.calculate_tx_records_for_offer` when evaluating an incoming offer, per `chia/wallet/trade_manager.py:692-702`).
3. Observe `condition.at("rf").atom` returns `None` at `chia/wallet/util/compute_hints.py:52`, triggering the unguarded `assert rf is not None` and raising an uncaught `AssertionError` instead of a handled validation error. [2](#0-1) [3](#0-2) 

**Note on coverage limits:** I was unable to retrieve the full body of `chia/wallet/wallet_state_manager.py` and the surrounding exception-handling context of `cat_wallet.py`/`did_wallet.py`/`cr_cat_wallet.py` call sites due to indexing limits, so I cannot definitively confirm whether the `AssertionError` is caught at every entry point versus propagating to crash a task. A Devin session with full repository access would be needed to trace each call site and confirm end-to-end impact before treating this as fully proven at High severity.

### Citations

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

**File:** chia/wallet/trade_manager.py (L692-720)
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
        valid_times: ConditionValidTimes = parse_timelock_info(
            parse_conditions_non_consensus(
                condition
                for spend in final_spend_bundle.coin_spends
                for condition in run(spend.puzzle_reveal, Program.from_serialized(spend.solution)).as_iter()
            )
        )
        # this executes the puzzles again
```
