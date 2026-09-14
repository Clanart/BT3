### Title
Unvalidated CREATE_COIN condition in NFT solution parsing triggers an unhandled `AssertionError` during wallet coin-state sync - ([File: chia/wallet/nft_wallet/nft_puzzle_utils.py])

### Summary
`get_metadata_and_phs()` parses the CLVM conditions produced by running an NFT's p2 (inner) puzzle against a solution taken directly from an incoming, attacker-controlled `CoinSpend`. It assumes a well-formed `CREATE_COIN` condition with a `memo` hint will always be present, and enforces this assumption with a bare `assert`. A counterparty who sends (or attempts to send) an NFT can craft a spend whose inner solution never emits such a condition, causing the assertion to fail with an unhandled `AssertionError` at the point this function is invoked from the wallet's sync pipeline.

### Finding Description
`get_metadata_and_phs()` runs the p2 puzzle with the innermost solution taken from an NFT `CoinSpend` and searches the resulting condition list for a `CREATE_COIN` (opcode 51) condition whose third argument (`atom`) is `1`, extracting a puzzle-hash "memo" from it: [1](#0-0) 

If no such condition is found — e.g., because the attacker crafts an inner solution whose p2 puzzle only outputs a `CREATE_COIN` without the memo hint, outputs no `CREATE_COIN` at all, or outputs one with `atom != 1` — `puzhash_for_derivation` stays `None` and line 259's `assert puzhash_for_derivation` raises an `AssertionError`.

This function is called directly, with no surrounding `try/except`, from two places in the core wallet sync path that process spends belonging to a coin the wallet is currently tracking or that hint to it as new:
- `NFTWallet.identify()`, invoked from `WalletStateManager.determine_coin_type()` while classifying every newly observed coin during `add_coin_states()` (the main peer-driven coin-state ingestion pipeline): [2](#0-1) [3](#0-2) 
- `NFTWallet.puzzle_solution_received()`, called once the coin has already been classified as an NFT belonging to this wallet: [4](#0-3) 

Because the wallet processes the puzzle reveal/solution of any spend it is notified about (via hints, subscriptions, or DID-linked NFT tracking) without first confirming that the counterparty's solution matches the "well-formed" shape the puzzle-driver code expects, a malicious spend crafted by an offer/NFT counterparty is sufficient to reach this code with attacker-controlled solution data.

### Impact Explanation
An unhandled `AssertionError` raised inside the coin-state classification path (`determine_coin_type` → `NFTWallet.identify` → `get_metadata_and_phs`) propagates out of the per-coin-state processing loop used by `add_coin_states()`, which is invoked from both trusted and untrusted sync (`WalletNode.add_states_from_peer` batches call into `wallet_state_manager.add_coin_states`). This can abort processing of the batch of coin states for the affected wallet, halting further sync/transaction-state updates until the wallet reconnects/retries — a spend-triggered transaction-processing halt analogous to the RGW NULL-pointer crash from malformed input in the reference CVE. Unlike a full node's mempool (which validates CLVM outputs strictly), the wallet's puzzle-driver "recognizer" code path here has a hard assumption enforced by `assert` rather than graceful rejection, so a single crafted NFT-related coin spend directed at (or hinting) a wallet can disrupt its sync loop.

### Likelihood Explanation
Reaching this code only requires that some NFT-shaped coin spend (an offer, gift, or hinted transfer) be observed by the victim's wallet during normal sync — no privileged access is needed, and the attacker fully controls the puzzle reveal/solution of the coin they create and later spend. The victim wallet does not need to accept an offer; simply being notified of the coin state (e.g., because the coin hints to one of the wallet's puzzle hashes, or is on the DID-linked launcher chain being tracked) is enough to invoke `UncurriedNFT.uncurry()` → `NFTWallet.identify()` → `get_metadata_and_phs()`.

### Recommendation
Replace the bare `assert puzhash_for_derivation` in `get_metadata_and_phs()` with an explicit, catchable error (e.g., raise a `ValueError`), and wrap all call sites (`NFTWallet.identify`, `NFTWallet.puzzle_solution_received`, `WalletStateManager.manual_nft_search`) in `try/except` so malformed/adversarial NFT solutions are logged and the coin is skipped rather than allowed to raise an unhandled exception in the middle of coin-state batch processing. Consider running the assertion logic under `-O`/production Python configurations that keep asserts enabled, but the primary fix is defensive exception handling around untrusted puzzle-solution parsing in the wallet sync path.

### Proof of Concept
1. Attacker creates a coin using the standard NFT ownership-layer/state-layer puzzle stack but curries a custom p2 (innermost) puzzle that, given any solution, outputs conditions that do **not** include a `CREATE_COIN` with a 1-valued third argument (memo hint) — e.g., it only emits `(CREATE_COIN puzhash amount)` with no hint list, or an unrelated condition.
2. Attacker spends this coin such that the resulting child coin's puzzle hash still matches an expected NFT puzzle shape and hints (or is otherwise made visible) to the victim wallet's puzzle hash / subscription.
3. When the victim wallet receives this coin state via `WalletNode.add_states_from_peer` → `WalletStateManager.add_coin_states` → `determine_coin_type`, `UncurriedNFT.uncurry()` succeeds and `NFTWallet.identify()` is invoked, which calls `get_metadata_and_phs(uncurried_nft, nft_data.parent_coin_spend.solution)`.
4. Because no valid memo-bearing `CREATE_COIN` condition exists, `puzhash_for_derivation` remains `None`, and `assert puzhash_for_derivation` raises `AssertionError`, propagating out of the coin-state processing call with no handling in this code path, aborting further processing of that coin-state batch for the victim wallet.

Note: I was not able to fully trace every outer caller of `add_coin_states()` to confirm whether some higher-level wrapper elsewhere in `wallet_node.py` catches generic `Exception` around the entire batch (which would reduce impact to "batch skipped and retried" rather than a persistent crash); this should be verified against the full `wallet_node.py` sync loop before treating the severity as final.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L205-205)
```python
        metadata, p2_puzzle_hash = get_metadata_and_phs(uncurried_nft, data.parent_coin_spend.solution)
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L291-296)
```python
        uncurried_nft: UncurriedNFT = nft_data.uncurried_nft
        old_p2_puzhash = uncurried_nft.p2_puzzle.get_tree_hash()
        _metadata, new_p2_puzhash = get_metadata_and_phs(
            uncurried_nft,
            nft_data.parent_coin_spend.solution,
        )
```
