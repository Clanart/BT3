Found a solid analog in `finish_graftroot_solutions`. This is a concrete, reachable, uncaught-crash bug in DataLayer offer processing that maps to the CVE's "missing validation of an untrusted structural value leading to an out-of-bounds/invalid-index access" bug class.

### Title
Unvalidated DL-offer graftroot singleton reference causes uncaught `KeyError` crash in offer finalization - (File: `chia/data_layer/data_layer_wallet.py`)

### Summary
`DataLayerWallet.finish_graftroot_solutions()` builds two dictionaries, `singleton_to_root` and `singleton_to_innerpuzhash`, keyed by the tree hash of DL singleton structs that are present as *non-ephemeral* DL singleton coin spends inside the offer bundle. It then iterates over a completely different, attacker-controlled list — `singleton_structs`, extracted by uncurrying the `graftroot` puzzle out of the *counterparty-supplied* solution of the offer — and indexes into those dictionaries with `singleton.get_tree_hash()` / `struct.get_tree_hash()` without checking that the key exists.

### Finding Description
The relevant code is: [1](#0-0) 
which populates the lookup tables strictly from the offer's own DL singleton coin spends, and [2](#0-1) 
which uncurries the `GRAFTROOT_DL_OFFERS` puzzle taken from `solution.at("rrffrf")` (part of the DL spend's *solution*, which is fully attacker-supplied content when taking an offer created/modified by a counterparty) and iterates `singleton_structs.as_iter()`, indexing `singleton_to_root[singleton.get_tree_hash()]` (line 1176) and `singleton_to_innerpuzhash[struct.get_tree_hash()]` (line 1195) with no membership check or `KeyError` handling.

If a malicious offer maker crafts a DL offer whose graftroot solution references a `singleton_struct` hash that is not present in the taker's own DL singleton spends (e.g., a bogus/foreign launcher id, or one deliberately mismatched from the values actually included in the offer's `coin_spends()`), `finish_graftroot_solutions` raises an uncaught `KeyError` instead of a graceful `ValueError`. This function is called directly from `TradeManager` (see `grep_search` match in `chia/wallet/trade_manager.py`) as part of taking a DataLayer offer, i.e., it is on the path a wallet client executes when accepting/finalizing an untrusted offer file received from a counterparty.

This mirrors the CVE-2016-2328 bug class: a structural/reference value taken from untrusted input (there, a "height" field; here, a `singleton_struct` hash reference) is used directly as an index/key into an internal structure without first validating that it corresponds to a value the code actually expects, producing an unhandled fault when processing crafted input.

### Impact Explanation
This is reachable by any wallet user who opens (attempts to take) a maliciously crafted `.offer` file involving DataLayer (mirror/store) assets. Rather than being rejected cleanly with a descriptive `ValueError` (as sibling checks in the same function do, e.g. "Malformed DL offer" and "One or more proofs of inclusion were invalid"), the crafted offer triggers an unhandled `KeyError`, crashing offer processing for that call path. This is a spend/offer-triggered transaction-processing halt confined to the DataLayer offer-taking flow — it does not, on its own, forge value or bypass conservation, but it denies service to a wallet user attempting a legitimate DL trade against a malicious counterparty's offer.

### Likelihood Explanation
Likely to trigger unintentionally with any offer whose graftroot dependency structs don't exactly line up with the offer's own DL spends, and trivially reproducible by an attacker who crafts an offer file with a mismatched `singleton_struct` in the `GRAFTROOT_DL_OFFERS` curried arguments. No special privileges are needed — only that the victim attempts to take (finish) the offer, which is the normal DL-offer trading flow.

### Recommendation
In `finish_graftroot_solutions`, validate that every `singleton.get_tree_hash()` / `struct.get_tree_hash()` referenced by the graftroot solution exists in `singleton_to_root` / `singleton_to_innerpuzhash` before indexing, and raise a `ValueError("Malformed DL offer")` (consistent with the existing error handling style in the same function) instead of allowing a `KeyError`/`AttributeError` to propagate.

### Proof of Concept
1. Construct (or modify) a DataLayer offer whose settlement spend's `graftroot` puzzle (`solution.at("rrffrf")`, uncurried to `GRAFTROOT_DL_OFFERS`) contains a `singleton_structs` list including at least one singleton struct whose `get_tree_hash()` does not match any DL singleton coin spend present in `offer.coin_spends()`.
2. Have the victim wallet call `DataLayerWallet.finish_graftroot_solutions(offer, solver)` (invoked from `TradeManager` when taking/finishing the offer) with a matching `values_to_prove`/`proofs_of_inclusion` solver entry so the earlier `sum(...)` check passes.
3. Execution reaches `singleton_to_root[singleton.get_tree_hash()]` at [3](#0-2)  or `singleton_to_innerpuzhash[struct.get_tree_hash()]` at [4](#0-3) , raising an uncaught `KeyError` instead of a clean rejection.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L1139-1148)
```python
        singleton_to_innerpuzhash: dict[bytes32, bytes32] = {}
        singleton_to_root: dict[bytes32, bytes32] = {}
        all_parent_ids: list[bytes32] = [cs.coin.parent_coin_info for cs in offer.coin_spends()]
        for spend in offer.coin_spends():
            matched, curried_args = match_dl_singleton(spend.puzzle_reveal)
            if matched and spend.coin.name() not in all_parent_ids:
                innerpuz, root_prg, launcher_id = curried_args
                singleton_struct = launcher_to_struct(bytes32(launcher_id.as_python())).get_tree_hash()
                singleton_to_root[singleton_struct] = bytes32(root_prg.as_python())
                singleton_to_innerpuzhash[singleton_struct] = innerpuz.get_tree_hash()
```

**File:** chia/data_layer/data_layer_wallet.py (L1161-1198)
```python
                mod, curried_args_prg = graftroot.uncurry()
                if mod == GRAFTROOT_DL_OFFERS:
                    _, singleton_structs, _, values_to_prove = curried_args_prg.as_iter()
                    all_proofs = []
                    roots = []
                    for singleton, values in zip(singleton_structs.as_iter(), values_to_prove.as_python()):
                        asserted_root: str | None = None
                        proofs_of_inclusion = []
                        for value in values:
                            for proof_of_inclusion in solver["proofs_of_inclusion"]:
                                root: str = proof_of_inclusion[0]
                                proof: tuple[int, list[bytes32]] = (proof_of_inclusion[1], proof_of_inclusion[2])
                                calculated_root: bytes32 = _simplify_merkle_proof(value, proof)
                                if (
                                    calculated_root == bytes32.from_hexstr(root)
                                    and calculated_root == singleton_to_root[singleton.get_tree_hash()]
                                ):
                                    proofs_of_inclusion.append(proof)
                                    if asserted_root is None:
                                        asserted_root = root
                                    elif asserted_root != root:
                                        raise ValueError("Malformed DL offer")
                                    break
                        roots.append(asserted_root)
                        all_proofs.append(proofs_of_inclusion)
                    if sum(len(proofs) for proofs in all_proofs) < sum(1 for _ in values_to_prove.as_iter()):
                        raise ValueError("One or more proofs of inclusion were invalid")
                    new_solution: Program = solution.replace(
                        rrffrrf=Program.to(
                            [
                                all_proofs,
                                [Program.to((bytes32.from_hexstr(root), None)) for root in roots if root is not None],
                                [ACS_MU_PH] * len(all_proofs),
                                [
                                    singleton_to_innerpuzhash[struct.get_tree_hash()]
                                    for struct in singleton_structs.as_iter()
                                ],
                                solution.at("rrffrrfrrrrf"),
```
