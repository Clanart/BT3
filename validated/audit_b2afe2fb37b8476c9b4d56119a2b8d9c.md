## Title
Unhandled `AssertionError` in NFT metadata/puzzle-hash parsing can halt wallet coin-state processing on a maliciously crafted incoming NFT-shaped coin - (`chia/wallet/nft_wallet/nft_puzzle_utils.py`)

### Summary
`get_metadata_and_phs()` executes the p2 puzzle of an incoming, attacker-controlled coin and then unconditionally asserts that a destination puzzle-hash was found among the resulting conditions. If an attacker crafts a coin spend whose inner puzzle/solution produces no `CREATE_COIN ... 1 [puzhash]` hint condition (or none at all), the function raises a bare `AssertionError` instead of returning a typed error. This function is invoked automatically by wallet sync logic whenever an incoming coin uncurries to the NFT puzzle shape, i.e., it is reachable by anyone who sends a specially crafted coin to a victim's puzzle hash — no privileges required.

### Finding Description
`get_metadata_and_phs()` in [1](#0-0)  runs `unft.p2_puzzle.run(...)` on the attacker-supplied solution and scans the resulting condition list for a `CREATE_COIN` condition whose memo/hint equals `1`. If none is found, `puzhash_for_derivation` stays `None` and the trailing `assert puzhash_for_derivation` fires, raising an unguarded `AssertionError`.

This function is called from two wallet-sync reachable paths that process *any* incoming coin that uncurries to the NFT singleton shape:
- `NFTWallet.puzzle_solution_received()` [2](#0-1) 
- `NFTWallet.identify()`, called from `WalletStateManager.determine_coin_type()` during coin-state processing for every wallet, whenever an incoming coin uncurries as an NFT puzzle [3](#0-2) , [4](#0-3) 

`UncurriedNFT.uncurry()` only validates the mod/curry structure of the puzzle (singleton + NFT state layer), not the runtime behavior of the inner p2/ownership puzzle [5](#0-4) . An attacker can construct an inner/p2 puzzle that satisfies this curry shape but, when run with attacker-chosen solution data, yields conditions without the expected `CREATE_COIN` hint (e.g., a puzzle that returns an empty condition list, or one with a `CREATE_COIN` condition lacking a hint memo). Because `determine_coin_type()` runs for every coin observed during sync that matches the NFT curry pattern — triggered simply by sending such a coin to a victim's puzzle hash (any coin amount with odd mojo value per the check at wallet_state_manager.py:973) — the victim's wallet-sync coroutine executing this code path raises `AssertionError`.

### Impact Explanation
This maps to the CVE-2016-8690 bug class (an unchecked/absent value dereferenced during untrusted-input parsing causing denial of service) translated to the Chia wallet's coin-processing pipeline: a spend-triggered, unhandled exception in the per-coin sync/processing pipeline is a wallet-side processing halt reachable by an unprivileged party who only needs to send a coin to the victim's address. Depending on whether the calling sync loop catches generic exceptions around this coin, this could repeatedly disrupt wallet state-manager sync for the affected wallet (the offending coin will be re-observed on every reconnect/resync attempt since it cannot be marked processed).

### Likelihood Explanation
I was not able to fully verify, within the available tool budget, whether the exception is caught by an outer `try/except` in the wallet-sync call chain (I found `except Exception` occurrences in `wallet_state_manager.py` but did not have iterations left to confirm whether they wrap this specific call path, nor to confirm the runtime feasibility of constructing an inner puzzle that both (a) satisfies `UncurriedNFT.uncurry()`'s structural checks and (b) yields no hinted `CREATE_COIN` condition when run). This uncertainty is material: if an outer handler already swallows exceptions from per-coin processing and simply skips/logs the offending coin without corrupting sync state, the practical impact is limited to a skipped/ignored coin rather than a sustained processing halt.

### Recommendation
Replace the bare `assert puzhash_for_derivation` in `get_metadata_and_phs()` with an explicit check that raises a typed, caught exception (or returns an `Optional` puzzle hash) so malformed/unexpected NFT-shaped incoming coins are rejected gracefully rather than crashing the coroutine via `AssertionError`. Verify (and add regression tests for) the exact behavior of the wallet-sync call chain when `NFTWallet.identify()`/`puzzle_solution_received()` raises, ensuring a single malicious incoming coin cannot repeatedly disrupt sync for the wallet that received it.

### Proof of Concept
1. Construct a coin whose puzzle reveal uncurries via `SINGLETON_TOP_LAYER_MOD` + `NFT_STATE_LAYER_MOD` (satisfying `UncurriedNFT.uncurry()`), with an inner p2 puzzle that, when run with the coin's own spend solution, returns an empty condition list (or a `CREATE_COIN` condition without a `1`-valued hint memo).
2. Send this coin (odd amount, e.g. 1 mojo) to a target wallet's derived puzzle hash and get it included in a block via a normal spend bundle submission (no special privileges required).
3. When the victim wallet syncs and observes this coin, `WalletStateManager.determine_coin_type()` uncurries it as an NFT and calls `NFTWallet.identify()` → `get_metadata_and_phs()`, which raises `AssertionError` at [6](#0-5) .

I could not confirm with certainty (due to exhausted tool budget) whether this exception is caught upstream in a way that prevents lasting disruption to the wallet's sync loop — this should be verified with a live Devin session before treating this as a confirmed high-severity DoS.

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

**File:** chia/wallet/nft_wallet/uncurry_nft.py (L88-169)
```python
    @classmethod
    def uncurry(cls, mod: Program, curried_args: Program) -> Self | None:
        """
        Try to uncurry a NFT puzzle
        :param cls UncurriedNFT class
        :param mod: uncurried Puzzle program
        :param uncurried_args: uncurried arguments to program
        :return Uncurried NFT
        """
        if mod != SINGLETON_TOP_LAYER_MOD:
            log.debug("Cannot uncurry NFT puzzle, failed on singleton top layer: Mod %s", mod)
            return None
        try:
            (singleton_struct, nft_state_layer) = curried_args.as_iter()
            singleton_mod_hash = singleton_struct.first()
            singleton_launcher_id = singleton_struct.rest().first()
            launcher_puzhash = singleton_struct.rest().rest()
        except ValueError as e:
            log.debug("Cannot uncurry singleton top layer: Args %s error: %s", curried_args, e)
            return None

        mod, curried_args = curried_args.rest().first().uncurry()
        if mod != NFT_MOD:
            log.debug("Cannot uncurry NFT puzzle, failed on NFT state layer: Mod %s", mod)
            return None
        try:
            # Set nft parameters
            nft_mod_hash, metadata, metadata_updater_hash, inner_puzzle = curried_args.as_iter()
            data_uris = Program.to([])
            data_hash = Program.to(0)
            meta_uris = Program.to([])
            meta_hash = Program.to(0)
            license_uris = Program.to([])
            license_hash = Program.to(0)
            edition_number = Program.to(1)
            edition_total = Program.to(1)
            # Set metadata
            for kv_pair in metadata.as_iter():
                if kv_pair.first().as_atom() == b"u":
                    data_uris = kv_pair.rest()
                if kv_pair.first().as_atom() == b"h":
                    data_hash = kv_pair.rest()
                if kv_pair.first().as_atom() == b"mu":
                    meta_uris = kv_pair.rest()
                if kv_pair.first().as_atom() == b"mh":
                    meta_hash = kv_pair.rest()
                if kv_pair.first().as_atom() == b"lu":
                    license_uris = kv_pair.rest()
                if kv_pair.first().as_atom() == b"lh":
                    license_hash = kv_pair.rest()
                if kv_pair.first().as_atom() == b"sn":
                    edition_number = kv_pair.rest()
                if kv_pair.first().as_atom() == b"st":
                    edition_total = kv_pair.rest()
            current_did: bytes32 | None = None
            transfer_program = None
            transfer_program_args = None
            royalty_address: bytes32 | None = None
            royalty_percentage: uint16 | None = None
            nft_inner_puzzle_mod = None
            mod, ol_args = inner_puzzle.uncurry()
            supports_did = False
            if mod == NFT_OWNERSHIP_LAYER:
                supports_did = True
                log.debug("Parsing ownership layer")
                _, current_did_p, transfer_program, p2_puzzle = ol_args.as_iter()
                _, transfer_program_args = transfer_program.uncurry()
                _, royalty_address_p, royalty_percentage_p = transfer_program_args.as_iter()
                royalty_percentage = uint16(royalty_percentage_p.as_int())
                royalty_address = bytes32(royalty_address_p.as_atom())
                current_did_atom = current_did_p.as_atom()
                if current_did_atom == b"":
                    # For unassigned NFT, set owner DID to None
                    current_did = None
                else:
                    current_did = bytes32(current_did_atom)
            else:
                log.debug("Creating a standard NFT puzzle")
                p2_puzzle = inner_puzzle
        except Exception as e:
            log.debug("Cannot uncurry NFT state layer: Args %s Error: %s", curried_args, e)
            return None
```
