### Title
Singleton fast-forward spends can commit to `AGG_SIG_PARENT`/`AGG_SIG_PARENT_AMOUNT`/`AGG_SIG_PARENT_PUZZLE` messages that are invalidated by rebasing, causing mempool-accepted transactions to fail final block validation - (File: chia/full_node/eligible_coin_spends.py)

### Summary
This is the same bug class as the reported `BLSSignatureAggregator` flaw: a piece of data used to build/verify a signature at *individual/admission-time* validation can diverge from the data used at *final/combined* validation, because state used to construct the signed message changes between the two checks. In Chia's singleton fast-forward (FF) mempool feature, a coin's `parent_coin_info` changes every time the spend is rebased onto the latest unspent singleton version by `perform_the_fast_forward()`, while `puzzle_hash` and `amount` are preserved by design.

### Finding Description
When a spend is admitted to the mempool, `MempoolManager.validate_spend_bundle()` marks it `ELIGIBLE_FOR_FF` and records `latest_singleton_lineage` [1](#0-0) . Later, at block-building time, `SingletonFastForward.process_fast_forward_spends()` calls `perform_the_fast_forward()`, which rewrites the coin's `parent_coin_info` (and its grandparent) to the singleton's newest unspent lineage, while explicitly preserving only `puzzle_hash` and `amount`: [2](#0-1) 

The signature the user/wallet produced when they built the original `SpendBundle` is fixed at submission time and cannot be updated afterward, since the mempool only rewrites the `CoinSpend`'s solution/coin, not the aggregated BLS signature. If the inner puzzle solution contains `AGG_SIG_PARENT`, `AGG_SIG_PARENT_AMOUNT`, or `AGG_SIG_PARENT_PUZZLE` conditions, the signed message includes `coin.parent_coin_info` per `make_aggsig_final_message()`: [3](#0-2) 

Because parent id is exactly the field fast-forward changes, a signature that was valid against the original (admission-time) parent id becomes invalid against the rebased parent id used when the transaction is actually included in a block. This mirrors the account-abstraction bug precisely: the individual-check-time identity (original parent coin) diverges from the combined/final-check-time identity (rebased parent coin), even though the same signature is reused.

The test suite's own fast-forward fixtures consistently avoid `AGG_SIG_PARENT`/`AGG_SIG_PARENT_AMOUNT`/`AGG_SIG_PARENT_PUZZLE` and use only `AGG_SIG_UNSAFE` (which is coin-independent) for FF-eligible singleton spends [4](#0-3) , and eligibility test comments only reference "conditions that disqualify" a spend from FF in a general sense [5](#0-4) , without this Python-level code showing an explicit, visible check that a spend using `AGG_SIG_PARENT`-family conditions is excluded from `ELIGIBLE_FOR_FF`. The actual computation of the `ELIGIBLE_FOR_FF` flag happens in the `chia_rs` (Rust) condition-parsing layer, which is not present in this indexed codebase, so I could not directly confirm or refute whether that layer disqualifies `AGG_SIG_PARENT*` conditions from fast-forward eligibility.

### Impact Explanation
If `AGG_SIG_PARENT`, `AGG_SIG_PARENT_AMOUNT`, or `AGG_SIG_PARENT_PUZZLE` conditions are not excluded from `ELIGIBLE_FOR_FF` classification, an attacker (or even an honest wallet using such a condition on a fast-forwardable singleton) could submit a spend bundle that:
1. Passes mempool admission (signature checked against the original coin's parent id, which matches at that instant).
2. Gets fast-forwarded during block construction once the singleton advances, silently rewriting `parent_coin_info` in the solution/coin.
3. Fails final consensus-level signature verification when the block is actually validated, because the AGG_SIG_PARENT message committed by the signature no longer matches the coin actually being spent.

This is a spend-triggered transaction-processing halt / invalid-inclusion class bug: the honest full node would build an invalid block (a self-inflicted denial of service on block production), or reject the transaction only after wasted CPU on fast-forward reconstruction, rather than at admission. It does not directly enable unauthorized fund movement, but it can cause coin-set divergence between nodes that fast-forward differently, or systematically break block templates containing that singleton lineage — a consensus-adjacent liveness/soundness issue, directly analogous to "aggregator gets throttled" in the original report (loss of availability/consistency due to a signature-identity mismatch introduced between two validation phases).

### Likelihood Explanation
Reaching this path requires only a single unprivileged spend-bundle submitter who crafts a singleton inner puzzle solution using `AGG_SIG_PARENT`/`AGG_SIG_PARENT_AMOUNT`/`AGG_SIG_PARENT_PUZZLE`, submitted via the standard RPC/mempool ingestion path — no special privileges, and singletons (NFTs, DIDs, pool/plotNFT singletons, DataLayer roots) are FF-eligible whenever their amount is odd and no disqualifying condition is present. However, likelihood is **uncertain** because the actual eligibility filter (what conditions disqualify a spend from `ELIGIBLE_FOR_FF`) is computed in the `chia_rs` Rust crate, which is outside this indexed Python codebase; I could not verify from available code whether `AGG_SIG_PARENT*` conditions are already excluded from FF eligibility by that layer. If they are excluded (as the design comments and all example fixtures strongly suggest, since only puzzle_hash/amount are guaranteed stable across FF), this specific analog would be mitigated by design and not exploitable.

### Recommendation
- Confirm in the `chia_rs` condition-parsing/flags logic that `ELIGIBLE_FOR_FF` is only set when none of `AGG_SIG_PARENT`, `AGG_SIG_PARENT_AMOUNT`, `AGG_SIG_PARENT_PUZZLE` (and any other condition whose message depends on a field that fast-forward rewrites) are present in the spend's conditions.
- Add an explicit Python-side defensive check in `chia/full_node/mempool_manager.py`'s FF-eligibility branch (around `chia/full_node/mempool_manager.py:728-746`) or in `perform_the_fast_forward()` (`chia/full_node/eligible_coin_spends.py:58-115`) that re-verifies, after fast-forwarding, that the item's conditions do not reference parent-id-dependent AGG_SIG opcodes, failing closed (falling back to non-FF / rejecting) if they do.
- Add a regression test analogous to `test_singleton_fast_forward_different_block` that specifically exercises a singleton with `AGG_SIG_PARENT` in its inner conditions to prove that either (a) it is correctly excluded from FF eligibility at admission, or (b) fast-forwarding it is rejected before block inclusion.

### Proof of Concept
Conceptual PoC (cannot be fully executed without access to the `chia_rs` FF-eligibility flag computation):
1. Launch and spend an eve singleton whose inner puzzle allows arbitrary conditions (e.g., `Program.to(13)` as used in `prepare_singleton_eve`, see [6](#0-5) ).
2. Build a singleton coin spend whose inner solution includes `[ConditionOpcode.AGG_SIG_PARENT, pubkey, msg]`, signed with `msg + coin.parent_coin_info + AGG_SIG_PARENT_ADDITIONAL_DATA` per `make_aggsig_final_message` (`chia/consensus/condition_tools.py:73-96`).
3. Submit this bundle to the mempool; confirm it is admitted with `supports_fast_forward == True` (as in `test_advancing_ff`, `chia/_tests/core/mempool/test_mempool_manager.py:2861-2929`).
4. Advance the singleton to a new unspent version on-chain via another spend (simulating normal singleton usage) so a fast-forward rebase is required.
5. Trigger block building (`create_block_generator2`), forcing `perform_the_fast_forward()` to rewrite `parent_coin_info` (`chia/full_node/eligible_coin_spends.py:83-97`).
6. Observe that the resulting block's `AGG_SIG_PARENT` message (computed against the *new* parent) no longer matches the originally signed message (computed against the *old* parent), so consensus-level signature validation of the produced block fails — demonstrating the mempool/individual check accepted a transaction that the final/combined validation rejects.

### Citations

**File:** chia/full_node/mempool_manager.py (L728-746)
```python
            lineage_info = None
            if bool(spend_conds.flags & ELIGIBLE_FOR_FF) and supports_fast_forward(coin_spend):
                # Make sure the fast forward spend still has a version that is
                # still unspent, because if the singleton has been spent in a
                # non-FF spend, this fast forward spend will never become valid.
                # So treat this as a normal spend, which requires the exact coin
                # to exist and be unspent.
                # Singletons that were created before the optimization of using
                # spent_index will also fail this test, and such spends will
                # fall back to be treated as non-FF spends.
                lineage_info = await get_unspent_lineage_info_for_puzzle_hash(spend_conds.puzzle_hash)
                if lineage_info is not None and not can_fast_forward_singleton(
                    unspent_lineage_info=lineage_info, coin=coin_spend.coin
                ):
                    # The latest unspent version of this singleton has a
                    # different amount than the coin we're spending, so this
                    # spend can never be fast forwarded onto it. Fall back to
                    # treating it as a normal spend.
                    lineage_info = None
```

**File:** chia/full_node/eligible_coin_spends.py (L81-97)
```python
    singleton_ph = spend_data.coin_spend.coin.puzzle_hash
    singleton_amount = spend_data.coin_spend.coin.amount
    new_coin = Coin(unspent_lineage_info.parent_id, singleton_ph, singleton_amount)
    new_parent = Coin(unspent_lineage_info.parent_parent_id, singleton_ph, singleton_amount)
    # The fast forward rebases the spend onto the latest unspent version by
    # keeping the spent coin's puzzle hash and amount. That only produces a
    # valid spend if both the latest unspent coin and its parent actually have
    # that same amount (their coin IDs are derived from it). This is guarded at
    # admission by can_fast_forward_singleton, but the latest unspent version
    # can advance further between admission and block building onto a version
    # whose (parent's) amount differs, so we reject here too rather than
    # asserting on reachable input.
    if new_coin.name() != unspent_lineage_info.coin_id or new_parent.name() != unspent_lineage_info.parent_id:
        raise ValueError("Cannot fast forward singleton onto a version with a different amount")
    new_solution = SerializedProgram.from_bytes(
        fast_forward_singleton(spend=spend_data.coin_spend, new_coin=new_coin, new_parent=new_parent)
    )
```

**File:** chia/consensus/condition_tools.py (L73-97)
```python
def make_aggsig_final_message(
    opcode: ConditionOpcode,
    msg: bytes,
    spend_conditions: Coin | SpendConditions,
    agg_sig_additional_data: dict[ConditionOpcode, bytes],
) -> bytes:
    if isinstance(spend_conditions, Coin):
        coin = spend_conditions
    elif isinstance(spend_conditions, SpendConditions):
        coin = Coin(spend_conditions.parent_id, spend_conditions.puzzle_hash, uint64(spend_conditions.coin_amount))
    else:
        raise ValueError(f"Expected Coin or Spend, got {type(spend_conditions)}")  # pragma: no cover

    COIN_TO_ADDENDUM_F_LOOKUP: dict[ConditionOpcode, Callable[[Coin], bytes]] = {
        ConditionOpcode.AGG_SIG_PARENT: lambda coin: coin.parent_coin_info,
        ConditionOpcode.AGG_SIG_PUZZLE: lambda coin: coin.puzzle_hash,
        ConditionOpcode.AGG_SIG_AMOUNT: lambda coin: int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PUZZLE_AMOUNT: lambda coin: coin.puzzle_hash + int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PARENT_AMOUNT: lambda coin: coin.parent_coin_info + int_to_bytes(coin.amount),
        ConditionOpcode.AGG_SIG_PARENT_PUZZLE: lambda coin: coin.parent_coin_info + coin.puzzle_hash,
        ConditionOpcode.AGG_SIG_ME: lambda coin: coin.name(),
    }
    addendum = COIN_TO_ADDENDUM_F_LOOKUP[opcode](coin)
    return msg + addendum + agg_sig_additional_data[opcode]

```

**File:** chia/_tests/core/mempool/test_singleton_fast_forward.py (L81-84)
```python
    # At this point, spends are considered *potentially* eligible for singleton
    # fast forward mainly when their amount is odd and they don't have conditions
    # that disqualify them
    conditions = [[ConditionOpcode.CREATE_COIN, IDENTITY_PUZZLE_HASH, test_amount]]
```

**File:** chia/_tests/core/mempool/test_singleton_fast_forward.py (L254-268)
```python
async def prepare_singleton_eve(
    sim: SpendSim, sim_client: SimClient, is_eligible_for_ff: bool, singleton_amount: uint64
) -> tuple[Program, CoinSpend, Program]:
    # Generate starting info
    key_lookup = KeyTool()
    pk = G1Element.from_bytes(public_key_for_index(1, key_lookup))
    starting_puzzle = p2_delegated_puzzle_or_hidden_puzzle.puzzle_for_pk(pk)
    if is_eligible_for_ff:
        # This program allows us to control conditions through solutions
        inner_puzzle = Program.to(13)
    else:
        inner_puzzle = starting_puzzle
    inner_puzzle_hash = inner_puzzle.get_tree_hash()
    # Get our starting standard coin created
    await sim.farm_block(starting_puzzle.get_tree_hash())
```

**File:** chia/_tests/core/mempool/test_singleton_fast_forward.py (L394-397)
```python
        inner_conditions: list[list[Any]] = [
            [ConditionOpcode.AGG_SIG_UNSAFE, bytes(g1), b"foobar"],
            [ConditionOpcode.CREATE_COIN, inner_puzzle_hash, SINGLETON_AMOUNT],
        ]
```
