### Title
Unvalidated CLVM condition-list indexing causes uncaught `IndexError` in `compute_memos_for_spend()` - (File: chia/wallet/util/compute_memos.py)

### Summary
`compute_memos_for_spend()` iterates over the CLVM output of running an untrusted `puzzle_reveal`/`solution` pair and indexes into each produced condition (`condition[0]`, `condition[1]`, `condition[2]`, `condition[3]`) without first checking that the condition has at least one element. [1](#0-0)  This mirrors the libgphoto2 bug class: an offset/field is read from attacker-influenced data before the buffer/structure is validated to actually contain that offset.

### Finding Description
`compute_memos_for_spend()` runs the coin's `puzzle_reveal` against its `solution` with `run_with_cost()` and converts the resulting CLVM condition list to Python objects via `.as_python()`. [2](#0-1)  It then unconditionally reads `condition[0]` to compare against `ConditionOpcode.CREATE_COIN`. [3](#0-2) 

A CLVM puzzle is fully attacker-controlled by whoever creates the `CoinSpend` (the wallet user itself, or, more importantly, any counterparty in a trade/offer whose spends get folded into a `WalletSpendBundle`, or any node/wallet that later re-derives memos for a spend it did not author). CLVM makes it trivial to produce a top-level "condition" that is an empty list/atom `()` (represented as `b""` after `.as_python()`), e.g. a puzzle body like `(q ())`. When `.as_python()` returns such an empty sequence as one of the entries in the condition list, indexing `condition[0]` raises an uncaught `IndexError`, since there is no length check analogous to the `len < PTP_oi_SequenceNumber` guard in the libgphoto2 report — here there is no guard at all before the indexed access.

`compute_memos_for_spend()` is invoked by `compute_memos()`, which loops over every `coin_spend` in a `WalletSpendBundle` and calls it without catching exceptions. [4](#0-3)  This utility is used by wallet-side code paths that need to compute memos for arbitrary spends (RPC endpoints, notification manager, clawback manager, CR-CAT wallet, wallet sync/state manager), based on `grep` results across `chia/wallet/wallet_rpc_api.py`, `chia/wallet/wallet_state_manager.py`, `chia/wallet/clawback_manager.py`, `chia/wallet/notification_manager.py`, and `chia/wallet/vc_wallet/cr_cat_wallet.py`. I was not able to fully confirm within the available iterations whether every one of these call sites wraps the call in a try/except, so it is uncertain how far the resulting exception propagates in each caller; this should be verified directly in those files.

### Impact Explanation
If any caller does not catch the `IndexError`, a single spend bundle (including one that reaches a wallet only as a counterparty's spend in an offer, or as a spend the wallet is simply syncing/displaying) can throw an unhandled exception in wallet code, halting whatever wallet-side transaction-processing/RPC flow invoked it (e.g., computing memos for a newly received transaction, building an RPC response, or displaying transaction details). This is a spend-triggered processing halt, which is a concrete, reachable Medium-severity impact analogous to the OOB-read report (both are "read past validated bounds due to a missing/incomplete length check on attacker-influenced data").

### Likelihood Explanation
Any wallet user, offer counterparty, or spend-bundle author can trivially craft a puzzle whose output conditions include an empty condition (e.g., `(q ())`), and this is common CLVM output shape (e.g. resulting from conditionally-empty condition lists or malformed/experimental delegated puzzles). No special privileges beyond crafting a normal `CoinSpend`/offer are required, and the flaw is purely a missing bounds/format check in Python.

### Recommendation
In `compute_memos_for_spend()`, validate the shape of each `condition` before indexing: skip/continue on any `condition` that is not a non-empty list (or explicitly check `len(condition) >= 1` before comparing `condition[0]`), and wrap `run_with_cost()`/list traversal in the same defensive style already used for `type(condition[3]) is not list`. Additionally, audit and harden all call sites (`wallet_rpc_api.py`, `wallet_state_manager.py`, `notification_manager.py`, `clawback_manager.py`, `cr_cat_wallet.py`) to ensure `compute_memos`/`compute_memos_for_spend` failures are caught and degrade gracefully rather than propagating an unhandled exception.

### Proof of Concept
1. Construct a `CoinSpend` whose `puzzle_reveal` is a CLVM program that returns a condition list containing at least one empty condition, e.g. `(q ())` — running this program yields a Python list containing `b""` as one "condition."
2. Include this `CoinSpend` in a `WalletSpendBundle` (as your own spend, or as a counterparty's spend inside an offer/aggregate bundle).
3. Trigger a wallet code path that calls `compute_memos()`/`compute_memos_for_spend()` on this bundle (e.g., via the wallet RPC that returns transaction memos, or via normal wallet sync processing of a received transaction).
4. `condition[0]` is executed on the empty `b""` entry inside `compute_memos_for_spend()`, raising `IndexError`, and (pending verification per call site) surfaces as an unhandled exception, halting that transaction-processing/RPC call. [5](#0-4)

### Citations

**File:** chia/wallet/util/compute_memos.py (L14-25)
```python
def compute_memos_for_spend(coin_spend: CoinSpend) -> dict[bytes32, list[bytes]]:
    _, result = run_with_cost(coin_spend.puzzle_reveal, INFINITE_COST, coin_spend.solution)
    memos: dict[bytes32, list[bytes]] = {}
    for condition in result.as_python():
        if condition[0] == ConditionOpcode.CREATE_COIN and len(condition) >= 4:
            # If only 3 elements (opcode + 2 args), there is no memo, this is ph, amount
            coin_added = Coin(coin_spend.coin.name(), bytes32(condition[1]), uint64(int_from_bytes(condition[2])))
            if type(condition[3]) is not list:
                # If it's not a list, it's not the correct format
                continue
            memos[coin_added.name()] = [mem for mem in condition[3] if isinstance(mem, bytes)]
    return memos
```

**File:** chia/wallet/util/compute_memos.py (L28-43)
```python
def compute_memos(bundle: WalletSpendBundle) -> dict[bytes32, list[bytes]]:
    """
    Retrieves the memos for additions in this spend_bundle, which are formatted as a list in the 3rd parameter of
    CREATE_COIN. If there are no memos, the addition coin_id is not included. If they are not formatted as a list
    of bytes, they are not included. This is expensive to call, it should not be used in full node code.
    """
    memos: dict[bytes32, list[bytes]] = {}
    for coin_spend in bundle.coin_spends:
        spend_memos = compute_memos_for_spend(coin_spend)
        for coin_name, coin_memos in spend_memos.items():
            existing_memos = memos.get(coin_name)
            if existing_memos is None:
                memos[coin_name] = coin_memos
            else:
                memos[coin_name] = existing_memos + coin_memos
    return memos
```
