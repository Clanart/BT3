## Title
Unhandled `assert` on attacker-controlled NFT spend solution crashes wallet coin-state processing - ([File: chia/wallet/nft_wallet/nft_puzzle_utils.py])

## Summary
`get_metadata_and_phs()` in `chia/wallet/nft_wallet/nft_puzzle_utils.py` runs the inner puzzle of an incoming coin's parent spend and then does `assert puzhash_for_derivation` without validating that a matching `CREATE_COIN` condition is actually present. This function is called from the wallet's untrusted coin-processing path (`NFTWallet.puzzle_solution_received`, `NFTWallet.identify`, and `WalletStateManager.manual_nft_search`) whenever a coin uncurries to the NFT puzzle template, which any peer/attacker can construct without owning the NFT. A bare `assert` failing here is an uncaught `AssertionError` that propagates out of coin processing, analogous to the reachable `CHECK`-failure DoS pattern in the TensorFlow report: a value assumed to always be well-formed (the derived puzzle hash / scalar) is instead attacker-controlled and can be missing, crashing the consuming code path instead of failing gracefully.

## Finding Description
`chia/wallet/nft_wallet/nft_puzzle_utils.py`:
```python
def get_metadata_and_phs(unft: UncurriedNFT, solution: SerializedProgram) -> tuple[Program, bytes32]:
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(Program.from_serialized(solution)))
    metadata = unft.metadata
    puzhash_for_derivation: bytes32 | None = None
    for condition in conditions.as_iter():
        if condition.list_len() < 2:
            continue
        condition_code = condition.first().as_int()
        if condition_code == -24:
            metadata = update_metadata(metadata, condition)
            metadata = Program.to(metadata)
        elif condition_code == 51:
            atom = condition.rest().rest().first().as_int()
            if atom == 1:
                if puzhash_for_derivation is not None:
                    continue
                memo = bytes32(condition.at("rrrff").as_atom())
                puzhash_for_derivation = memo
    assert puzhash_for_derivation
    return metadata, puzhash_for_derivation
``` [1](#0-0) 

The function assumes the p2-layer's output conditions will always contain exactly one `CREATE_COIN` (opcode 51) with a memo list whose first element equals `1` (the NFT "recreate to new owner" convention). This is a puzzle-behavior assumption, not something enforced by any Rust/CLVM validation layer — a hostile spend of a coin that curries into the NFT ownership/state-layer template (which the attacker fully controls when constructing that coin's puzzle/solution) can produce conditions that never satisfy this pattern (e.g. `CREATE_COIN` with a memo list `[2]` or no memos at all, or no `CREATE_COIN` at all).

This function is invoked from wallet coin-sync code that must process any coin the wallet is told about, before it is known to be "owned" or trusted:
- `NFTWallet.puzzle_solution_received()`, called from `NFTWallet.coin_added()` when the wallet state manager has already uncurried an incoming coin as an NFT puzzle: [2](#0-1) 
- `NFTWallet.identify()`, called for every coin the sync path classifies as NFT-shaped via `UncurriedNFT.uncurry()` in `WalletStateManager.determine_coin_type()`: [3](#0-2) 
- `WalletStateManager.manual_nft_search()`, an RPC-triggered path that fetches a coin spend by id and calls `get_metadata_and_phs` directly: [4](#0-3) 

None of these call sites wrap the call in `try/except`, so the bare `assert` failure is an uncaught `AssertionError`.

## Impact Explanation
This is a spend-triggered halt of transaction/coin-state processing analogous to the report's "CHECK-failure causes process termination" bug class: an attacker crafts a puzzle that curries to the recognized NFT template shape (satisfying `UncurriedNFT.uncurry()`) but whose p2-layer conditions omit the expected single tagged `CREATE_COIN`. Once such a coin is created on-chain (attacker only needs to spend their own coin to construct it — no privilege required) and the target wallet syncs past it (via `coin_added`/`determine_coin_type`, or a user invoking `manual_nft_search` on it), the wallet client crashes/errors out of its coin-processing routine with an unhandled `AssertionError` instead of cleanly rejecting the malformed NFT. Depending on how the wallet's coin-state-processing loop and retry logic handle this (not verifiable with available tooling in this pass), this can repeatedly disrupt wallet sync for any wallet that receives or observes such a coin, denying wallet-user coin processing.

## Likelihood Explanation
Any unprivileged user can construct and broadcast a spend bundle whose puzzle reveal curries to the NFT state-layer/ownership-layer mod hashes recognized by `UncurriedNFT.uncurry()`, and whose inner (p2) puzzle returns conditions violating the assumed `CREATE_COIN`+memo=`[1]` shape — this requires only CLVM puzzle-authoring skill, no signing keys of the victim, and no consensus-level restriction prevents such a puzzle from being confirmed on-chain (the puzzle just won't be a "real" NFT to other software, but nothing stops it from existing as a coin). Any peer wallet that syncs this coin (e.g., it's sent/hinted to that wallet, or the user manually inspects it via `manual_nft_search`) hits the same code path.

## Recommendation
Replace the bare `assert puzhash_for_derivation` in `get_metadata_and_phs()` with an explicit, catchable error (e.g., raise `ValueError`) and ensure all call sites in `nft_wallet.py` and `wallet_state_manager.py` handle malformed/foreign NFT-shaped coins gracefully (log and skip / mark unrecognized) rather than propagating an assertion failure into the wallet sync loop. Audit other `assert` usages in `chia/wallet/nft_wallet/nft_puzzle_utils.py` and `uncurry_nft.py` that operate on solution-derived data from untrusted incoming coins for the same pattern.

## Proof of Concept
1. Construct a coin whose puzzle reveal curries with `SINGLETON_MOD` + `NFT_STATE_LAYER_MOD` + `NFT_OWNERSHIP_LAYER` (or plain state-layer) so that `UncurriedNFT.uncurry()` recognizes it as an NFT, satisfying `WalletStateManager.determine_coin_type()`'s NFT branch. [3](#0-2) 
2. Set the inner p2 puzzle to return conditions that create a coin but with a memo list that is empty or does not start with `1` (or omit `CREATE_COIN` entirely), so the loop in `get_metadata_and_phs` never sets `puzhash_for_derivation`. [5](#0-4) 
3. Spend this coin on-chain (self-funded, no other party needed) and let/cause a target wallet to sync the resulting coin state (e.g., include the victim's puzzle hash as a hint, or have the victim call `manual_nft_search` on the coin id).
4. Observe the wallet's coin-processing call into `get_metadata_and_phs` raise `AssertionError`, propagating out of `NFTWallet.identify`/`puzzle_solution_received`/`manual_nft_search` uncaught.

Note: I could not fully verify within the available tooling whether the outer coin-state processing loop in `WalletStateManager` (`_add_coin_states` or equivalent) wraps per-coin processing in a try/except that would downgrade this to a per-coin failure rather than a full crash/loop-halting exception; this determines whether the ultimate impact is "processing halt for that sync batch" versus a more contained failure, and should be confirmed against the actual call chain before finalizing severity.

### Citations

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L234-260)
```python
def get_metadata_and_phs(unft: UncurriedNFT, solution: SerializedProgram) -> tuple[Program, bytes32]:
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(Program.from_serialized(solution)))
    metadata = unft.metadata
    puzhash_for_derivation: bytes32 | None = None
    for condition in conditions.as_iter():
        if condition.list_len() < 2:
            # invalid condition
            continue
        condition_code = condition.first().as_int()
        log.debug("Checking condition code: %r", condition_code)
        if condition_code == -24:
            # metadata update
            metadata = update_metadata(metadata, condition)
            metadata = Program.to(metadata)
        elif condition_code == 51:
            atom = condition.rest().rest().first().as_int()

            if atom == 1:
                # destination puzhash
                if puzhash_for_derivation is not None:
                    # ignore duplicated create coin conditions
                    continue
                memo = bytes32(condition.at("rrrff").as_atom())
                puzhash_for_derivation = memo
                log.debug("Got back puzhash from solution: %s", puzhash_for_derivation)
    assert puzhash_for_derivation
    return metadata, puzhash_for_derivation
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L203-206)
```python
        singleton_id = uncurried_nft.singleton_launcher_id
        parent_inner_puzhash = uncurried_nft.nft_state_layer.get_tree_hash()
        metadata, p2_puzzle_hash = get_metadata_and_phs(uncurried_nft, data.parent_coin_spend.solution)
        self.log.debug("Got back puzhash from solution: %s", p2_puzzle_hash)
```

**File:** chia/wallet/wallet_state_manager.py (L965-976)
```python
        # Check if the coin is a NFT
        #                                                        hint
        # First spend where 1 mojo coin -> Singleton launcher -> NFT -> NFT
        uncurried_nft = (
            UncurriedNFT.uncurry(uncurried.mod, Program.to(uncurried.curried_args))
            if uncurried.mod is not None and uncurried.curried_args is not None
            else None
        )
        if uncurried_nft is not None and coin_state.coin.amount % 2 == 1:
            nft_data = NFTCoinData(uncurried_nft, parent_coin_state, coin_spend)
            return await NFTWallet.identify(self, nft_data, sync_scope), nft_data

```

**File:** chia/wallet/wallet_state_manager.py (L2352-2363)
```python
    async def manual_nft_search(self, coin_id: bytes32, latest: bool = True) -> ManualNFTSearchResults:
        # Get coin state
        peer = self.wallet_node.get_full_node_peer()
        coin_spend, coin_state = await self.get_latest_singleton_coin_spend(peer, coin_id, latest)
        # convert to NFTInfo
        # Check if the metadata is updated
        full_puzzle: Program = Program.from_bytes(bytes(coin_spend.puzzle_reveal))

        uncurried_nft: UncurriedNFT | None = UncurriedNFT.uncurry(*full_puzzle.uncurry())
        if uncurried_nft is None:
            raise ValueError("The coin is not a NFT.")
        metadata, p2_puzzle_hash = get_metadata_and_phs(uncurried_nft, coin_spend.solution)
```
