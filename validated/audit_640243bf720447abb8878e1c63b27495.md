### Title
Unbounded CLVM execution via `INFINITE_COST` in wallet memo computation on attacker-controlled offer/transaction puzzles - (File: chia/wallet/util/compute_memos.py)

### Summary
The Apache CVE describes unbounded resource allocation while processing externally supplied OCSP data with no cost/size limit. The analogous pattern in chia-blockchain is `compute_memos_for_spend()`, which executes an attacker-supplied `puzzle_reveal`/`solution` pair with `INFINITE_COST` instead of the mempool's metered cost limit, whenever a wallet needs to derive memos for a coin spend that did not originate from the wallet's own trusted spend-bundle construction (e.g., spends coming from a counterparty's offer, a received transaction, or a clawback claim). [1](#0-0) 

### Finding Description
`compute_memos_for_spend()` calls `run_with_cost(coin_spend.puzzle_reveal, INFINITE_COST, coin_spend.solution)`, deliberately bypassing the cost metering that `MempoolManager.pre_validate_spendbundle()` enforces (`max_tx_clvm_cost`, plus the post-execution atom/pair density checks) before any full-node/mempool CLVM execution is allowed to run to completion. [2](#0-1) 

`compute_memos()` iterates over every `coin_spend` in a `WalletSpendBundle` and calls this unmetered execution path for each one: [3](#0-2) 

This function is invoked from wallet code paths that process spend bundles/coin spends whose puzzle reveals are not authored by the local wallet — e.g. CAT/CR-CAT trading (`chia/wallet/vc_wallet/cr_cat_wallet.py`), clawback handling (`chia/wallet/clawback_manager.py`), notifications (`chia/wallet/notification_manager.py`), and general wallet state sync/RPC (`chia/wallet/wallet_state_manager.py`, `chia/wallet/wallet_rpc_api.py`). In all of these flows the puzzle reveal and solution embedded in the coin spend can be crafted by an offer counterparty or a peer supplying transaction data, and `run_with_cost` with `INFINITE_COST` will run that CLVM program to completion regardless of how expensive or memory-intensive it is, unlike the identically-shaped call used for offers in `chia/wallet/trading/offer.py`, which is documented as similarly unmetered.

Because `INFINITE_COST` removes any cost ceiling, a maliciously crafted puzzle/solution (e.g., one that performs deep recursive `concat`/pair construction, similar in spirit to the "malicious generator" patterns already tested against the mempool's metered path in `chia/_tests/core/mempool/test_mempool.py`) can force the wallet process executing `compute_memos_for_spend` to consume unbounded CPU and memory, since none of the mempool's atom/pair/cost limits apply in this code path. [4](#0-3) 

### Impact Explanation
A wallet that receives a crafted offer, incoming transaction, or notification containing a coin spend with an expensive/looping puzzle can be driven into unbounded CLVM execution while trying to compute memos for its own transaction bookkeeping. This can stall or crash wallet transaction processing (a spend-triggered transaction-processing halt) for any wallet user or offer counterparty who evaluates the malicious spend bundle, independent of and prior to the properly cost-metered mempool/full-node CLVM path.

### Likelihood Explanation
Any unprivileged offer counterparty or peer who can get a wallet to inspect a spend bundle (offer file, incoming transaction, notification, clawback claim) controls the puzzle reveal/solution content and can trivially construct a program that is cheap to encode but extremely expensive to execute (as already demonstrated by the "malicious generator" test fixtures used against the metered mempool path). Because `compute_memos_for_spend` applies no cost limit at all, exploitation only requires convincing a wallet to process such a spend bundle through one of the affected wallet flows.

### Recommendation
Replace `INFINITE_COST` in `compute_memos_for_spend()` (and the equivalent unmetered calls in `chia/wallet/trading/offer.py`) with a bounded cost limit consistent with the mempool's per-spend cost ceiling (e.g., `max_tx_clvm_cost` or a dedicated wallet-side cap), and reject/skip memo computation for spends that exceed it rather than allowing unbounded execution.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` is a small CLVM program that performs deep self-recursive `concat`/pair growth (structurally similar to the `SINGLE_ARG_INT_LADDER_COND`/`large_string` patterns used in `chia/_tests/core/mempool/test_mempool.py`) so that executing it costs far more than the mempool's `max_tx_clvm_cost` would ever allow. Embed this coin spend in a `WalletSpendBundle` delivered as an offer, incoming transaction, or clawback claim to a victim wallet, and call `compute_memos()`/`compute_memos_for_spend()` on it (as the wallet flows in `chia/wallet/vc_wallet/cr_cat_wallet.py`, `chia/wallet/clawback_manager.py`, and `chia/wallet/notification_manager.py` do). Because `run_with_cost` is invoked with `INFINITE_COST`, execution proceeds without any of the mempool's cost/atom/pair limits, consuming excessive CPU/memory on the victim's wallet process. [1](#0-0)

### Citations

**File:** chia/wallet/util/compute_memos.py (L14-16)
```python
def compute_memos_for_spend(coin_spend: CoinSpend) -> dict[bytes32, list[bytes]]:
    _, result = run_with_cost(coin_spend.puzzle_reveal, INFINITE_COST, coin_spend.solution)
    memos: dict[bytes32, list[bytes]] = {}
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

**File:** chia/_tests/core/mempool/test_mempool.py (L2622-2634)
```python
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
