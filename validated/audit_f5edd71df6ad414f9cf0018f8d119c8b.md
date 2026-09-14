I found a strong analog. Now finalizing the answer with confirmed root cause details.### Title
Unhandled assertion crash on missing destination-puzzlehash memo in NFT transfer processing - (File: chia/wallet/nft_wallet/nft_puzzle_utils.py)

### Summary
`get_metadata_and_phs()` in `chia/wallet/nft_wallet/nft_puzzle_utils.py` runs the inner puzzle of an incoming NFT coin spend and scans the resulting conditions for a `CREATE_COIN` (opcode 51) condition whose memo (`condition.at("rrrff")`) supplies the new p2 puzzle hash. If no such condition is found, `puzhash_for_derivation` stays `None` and the function does a bare `assert puzhash_for_derivation`, raising an unhandled `AssertionError` instead of returning a typed error. This is the direct analog of CVE-2018-18192's `DLS::File::GetFirstSample()` NULL pointer dereference: a required-but-absent value is dereferenced/asserted without a guarded, recoverable error path, and the input that determines whether the value exists is attacker-controlled.

### Finding Description
`get_metadata_and_phs()`:
```
chia/wallet/nft_wallet/nft_puzzle_utils.py:234-260
def get_metadata_and_phs(unft: UncurriedNFT, solution: SerializedProgram) -> tuple[Program, bytes32]:
    ...
    puzhash_for_derivation: bytes32 | None = None
    for condition in conditions.as_iter():
        ...
        elif condition_code == 51:
            atom = condition.rest().rest().first().as_int()
            if atom == 1:
                if puzhash_for_derivation is not None:
                    continue
                memo = bytes32(condition.at("rrrff").as_atom())
                puzhash_for_derivation = memo
    assert puzhash_for_derivation
    return metadata, puzhash_for_derivation
```
This function is invoked with attacker-influenced data from two reachable call sites processing incoming NFT coin spends during normal wallet sync:
- `chia/wallet/nft_wallet/nft_wallet.py:205` in `puzzle_solution_received()`, called from `coin_added()`/`_add_coin_state()` whenever the wallet syncs a coin that was uncurried as an NFT.
- `chia/wallet/nft_wallet/nft_wallet.py:293` in `NFTWallet.identify()`, called from `WalletStateManager.determine_coin_type()` (`chia/wallet/wallet_state_manager.py:965-975`) during coin-state processing.

The `conditions` are the output of running the NFT's own p2 (ownership/inner) puzzle with the solution taken from `data.parent_coin_spend.solution` — i.e., **the spender who created/updated the NFT controls the puzzle reveal and solution**, and therefore controls whether a `CREATE_COIN` condition with amount `1` (destination puzzlehash marker) and a valid memo is emitted at all. Any counterparty (offer taker/maker, DID/NFT sender, or anyone constructing a spend that creates a coin recognized by the receiving wallet as this NFT's singleton) can craft a spend whose inner puzzle/solution produces no such condition, or a `CREATE_COIN` condition with amount 1 but a malformed/missing 4th argument list (making `condition.at("rrrff")` fail or return nil), causing the `assert` to fail with `AssertionError` instead of being handled as an invalid/malformed spend.

### Impact Explanation
This is a spend-triggered halt: a wallet processing an incoming coin state (during normal sync, offer settlement, or WalletRPC calls like `manual_nft_search`) can be crashed with an unhandled exception when it encounters a maliciously/incorrectly constructed spend that superficially uncurries as an NFT singleton but doesn't produce the expected `CREATE_COIN`/memo output. Because `_add_coin_state`/`determine_coin_type` are core parts of wallet sync's coin-processing loop, an uncaught `AssertionError` here can abort the sync batch, disrupting the wallet's ability to process subsequent coin states and potentially requiring a wallet restart/resync — a mempool/wallet-processing halt analogous to a NULL-deref-induced crash, though scoped to the wallet's sync loop rather than the full node consensus path. It does not directly cause fund loss, forged identity, or invalid block acceptance, keeping this class closer to a availability/DoS issue against the receiving wallet process rather than a supply/inflation bug.

### Likelihood Explanation
Reachability is high: any spend of a coin whose puzzle uncurries to `SINGLETON_TOP_LAYER_MOD` + `NFT_STATE_LAYER_MOD` layout (which is public/known) with an amount that is odd (NFTs use odd-amount singleton convention) will be routed into `NFTWallet.identify()`/`puzzle_solution_received()` regardless of who created it, since `determine_coin_type()` only checks the puzzle structure, not trust in the sender. Constructing a puzzle/solution pair for the p2 layer that yields no matching `CREATE_COIN` condition (or an incorrectly shaped one) requires only standard CLVM crafting skill, no cryptographic material, and no privileged network position — consistent with an "offer counterparty" or arbitrary on-chain spend triggering wallet-side processing of the resulting coin.

### Recommendation
Replace the bare `assert puzhash_for_derivation` in `get_metadata_and_phs()` with an explicit check that raises a caught/handled exception type (e.g. a domain-specific `ValueError`/`InvalidNFTSpend`), and ensure all call sites (`nft_wallet.py` `puzzle_solution_received()`, `NFTWallet.identify()`, `WalletStateManager.manual_nft_search()`/`find_lost_did()` paths that call it) catch this condition and gracefully skip/ignore the malformed coin instead of propagating an unhandled exception through the sync loop. Additionally, harden `condition.at("rrrff")` access with bounds/structure validation rather than relying on `Program.at()` raising uncaught `EvalError`/`ValueError` on malformed conditions.

### Proof of Concept
1. Construct an NFT-shaped puzzle (`SINGLETON_TOP_LAYER_MOD` curried with an `NFT_STATE_LAYER_MOD` inner puzzle) whose p2/ownership inner puzzle, when run against attacker-chosen solution, yields conditions containing no `(51 puzhash amount memo)` condition with `amount == 1`, or a `(51 ... 1 ...)` condition whose 4th argument is not a valid 32-byte memo.
2. Spend the coin with this puzzle/solution on-chain (or simulate it as a coin_spend fed into wallet sync).
3. A wallet that is tracking/recognizes this coin as an NFT singleton (odd amount, matching puzzle structure) will call `get_metadata_and_phs()` via `puzzle_solution_received()` or `NFTWallet.identify()`, hit `assert puzhash_for_derivation` (or an `EvalError` from `condition.at("rrrff")`), and raise an unhandled exception during coin-state processing, disrupting `_add_coin_state()`/wallet sync. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L189-214)
```python
    async def puzzle_solution_received(
        self, coin: Coin, data: NFTCoinData, peer: WSChiaConnection, sync_scope: WalletSyncScope
    ) -> None:
        self.log.debug("Puzzle solution received to wallet: %s", self.wallet_info)
        # At this point, the puzzle must be a NFT puzzle.
        # This method will be called only when the wallet state manager uncurried this coin as a NFT puzzle.

        uncurried_nft: UncurriedNFT = data.uncurried_nft
        self.log.debug(
            "found the info for NFT coin %s %s %s",
            coin.name().hex(),
            uncurried_nft.inner_puzzle,
            uncurried_nft.singleton_struct,
        )
        singleton_id = uncurried_nft.singleton_launcher_id
        parent_inner_puzhash = uncurried_nft.nft_state_layer.get_tree_hash()
        metadata, p2_puzzle_hash = get_metadata_and_phs(uncurried_nft, data.parent_coin_spend.solution)
        self.log.debug("Got back puzhash from solution: %s", p2_puzzle_hash)
        self.log.debug("Got back updated metadata: %s", metadata)
        derivation_record: (
            DerivationRecord | None
        ) = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(p2_puzzle_hash)
        self.log.debug("Record for %s is: %s", p2_puzzle_hash, derivation_record)
        if derivation_record is None:
            self.log.debug("Not our NFT, pointing to %s, skipping", p2_puzzle_hash)
            return
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L274-296)
```python
    @classmethod
    async def identify(
        cls,
        wallet_state_manager: WalletStateManager,
        nft_data: NFTCoinData,
        sync_scope: WalletSyncScope,
    ) -> WalletIdentifier | None:
        """
        Handle the new coin when it is a NFT
        :param nft_data: all necessary data to process a NFT coin
        :return: Wallet ID & Wallet Type
        """
        wallet_identifier = None
        # DID ID determines which NFT wallet should process the NFT
        new_did_id: bytes32 | None = None
        old_did_id = None
        # P2 puzzle hash determines if we should ignore the NFT
        uncurried_nft: UncurriedNFT = nft_data.uncurried_nft
        old_p2_puzhash = uncurried_nft.p2_puzzle.get_tree_hash()
        _metadata, new_p2_puzhash = get_metadata_and_phs(
            uncurried_nft,
            nft_data.parent_coin_spend.solution,
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

**File:** chia/wallet/wallet_state_manager.py (L2352-2364)
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
        # Note: This is not the actual unspent NFT full puzzle.
```
