## Title
Unhandled `assert` in NFT metadata/derivation parsing lets an attacker-crafted on-chain coin crash wallet coin-state processing - (File: chia/wallet/nft_wallet/nft_puzzle_utils.py)

### Summary
`get_metadata_and_phs()` in `chia/wallet/nft_wallet/nft_puzzle_utils.py` runs the untrusted p2 (inner) puzzle of any coin that structurally uncurries as an NFT (`UncurriedNFT`) and then does a bare `assert puzhash_for_derivation` at the end, with no `try/except` around the call sites. [1](#0-0)  This function is invoked unconditionally from `NFTWallet.identify()`, which is reached from `WalletStateManager.determine_coin_type()` for any coin that satisfies the NFT-shaped uncurry pattern and an odd amount check — a shape any spend-bundle submitter can construct on-chain without going through the normal minting flow. [2](#0-1) [3](#0-2) 

### Finding Description
`get_metadata_and_phs()` walks the conditions produced by running `unft.p2_puzzle` against the attacker-supplied solution, looking for a `CREATE_COIN` (opcode 51) condition whose third argument (`amount`) is `1` in order to extract the destination puzzle hash for the singleton child (`puzhash_for_derivation`). If the attacker's puzzle/solution produces no such condition (e.g., the puzzle simply outputs no conditions, or only `CREATE_COIN` conditions with a different amount, or raises inside CLVM in a way that still returns cleanly), `puzhash_for_derivation` remains `None`, and the trailing `assert puzhash_for_derivation` raises an uncaught `AssertionError`. [1](#0-0) 

This is directly analogous to the CVE's bug class: attacker-controlled protocol/puzzle input reaching a code path that performs an unchecked/unbounded operation (there: array bound; here: an `assert` invariant) that the parser assumes will always hold, causing the parsing routine to abort abnormally instead of gracefully rejecting malformed input.

The reachability path is:
1. `WalletStateManager.determine_coin_type()` uncurries an arbitrary coin's puzzle and, if it matches the `UncurriedNFT` shape and the coin amount is odd, calls `NFTWallet.identify()` unconditionally — no ownership check happens first. [3](#0-2) 
2. `NFTWallet.identify()` immediately calls `get_metadata_and_phs(uncurried_nft, nft_data.parent_coin_spend.solution)` before any derivation-record ownership check is performed. [2](#0-1) 
3. `get_metadata_and_phs` executes the attacker-controlled inner puzzle/solution and can hit the unguarded `assert`. [1](#0-0) 

Because any attacker can construct a coin whose puzzle reveal matches the `SINGLETON_TOP_LAYER_MOD` / `NFT_STATE_LAYER_MOD` curry shape (these are just standard, publicly known Chialisp mods; nothing prevents a third party from currying them with arbitrary metadata/inner puzzle and creating such a coin with an odd amount via a normal spend), this is reachable purely by placing a maliciously-shaped coin on-chain — no privileged, peer, or node-operator access is required. Any wallet that subsequently processes this coin during ordinary sync (all full nodes broadcast confirmed coin states) will run the crafted inner puzzle inside `get_metadata_and_phs` and can trip the assertion.

### Impact Explanation
An uncaught `AssertionError` propagating out of `get_metadata_and_phs` during `determine_coin_type`/`identify` breaks the wallet's coin-state processing loop for the batch containing the malicious coin. Depending on how the caller handles exceptions in the sync pipeline, this can repeatedly stall or crash wallet sync/transaction processing for any wallet that observes the crafted coin, i.e., a spend-triggered transaction-processing halt for third-party wallets — not merely the attacker's own wallet. This matches the "spend-triggered transaction-processing halt" impact category. It does not enable coin theft, inflation, or consensus divergence; the full node/consensus layer is unaffected, only the wallet-side coin ingestion pipeline.

### Likelihood Explanation
Likelihood is moderate-to-high for triggering the crash condition (constructing a coin with the correct curry shape and an inner puzzle producing no `CREATE_COIN` amount-1 condition is straightforward CLVM puzzle authoring), but the actual severity depends on unverified exception-handling behavior of the wallet sync call stack above `determine_coin_type()` — I could not confirm within the available context whether a caller wraps `determine_coin_type()`/`identify()` in a try/except that would contain the crash to a single coin versus letting it kill the sync task entirely. This uncertainty should be resolved by inspecting the full call chain (`WalletStateManager` sync handlers) in a live session before treating this as a confirmed high-severity DoS.

### Recommendation
Replace the bare `assert puzhash_for_derivation` in `get_metadata_and_phs` with an explicit check that raises a caught/expected exception type (e.g., `ValueError`), and wrap calls to `get_metadata_and_phs`/`get_new_owner_did`/`recurry_nft_puzzle` in `NFTWallet.identify()` and `NFTWallet.puzzle_solution_received()` with error handling that safely skips/ignores malformed "NFT-shaped" coins instead of propagating an exception into the sync loop. Additionally, consider deferring execution of untrusted inner puzzles until after an ownership/derivation-record check succeeds, reducing exposure to arbitrary third-party coins.

### Proof of Concept
Conceptual PoC (requires a live Devin session with full repo/test harness to fully execute and confirm the crash and its blast radius):
1. Construct a puzzle using the standard `SINGLETON_TOP_LAYER_MOD` curried with `NFT_STATE_LAYER_MOD`, an arbitrary `metadata`, `metadata_updater_hash`, and an inner puzzle (`p2_puzzle`) that is simply `(mod (solution) ())` — i.e., always returns an empty condition list regardless of solution.
2. Mint/create a coin using this puzzle with an odd amount via a normal spend bundle (no minting-flow legitimacy required — this is just a puzzle-hash-matching coin).
3. Spend that coin (any solution).
4. Have any wallet (not the attacker's) process the resulting coin state during sync; `WalletStateManager.determine_coin_type()` will match it as NFT-shaped and call `NFTWallet.identify()`, which calls `get_metadata_and_phs()`, hitting `assert puzhash_for_derivation` with `puzhash_for_derivation is None`.
5. Confirm whether the resulting `AssertionError` is caught anywhere up the call stack; if uncaught, it disrupts/crashes the wallet's coin-state processing for that sync batch.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L291-300)
```python
        uncurried_nft: UncurriedNFT = nft_data.uncurried_nft
        old_p2_puzhash = uncurried_nft.p2_puzzle.get_tree_hash()
        _metadata, new_p2_puzhash = get_metadata_and_phs(
            uncurried_nft,
            nft_data.parent_coin_spend.solution,
        )
        if uncurried_nft.supports_did:
            parsed_did_id = get_new_owner_did(
                uncurried_nft, Program.from_serialized(nft_data.parent_coin_spend.solution)
            )
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
