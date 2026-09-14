### Title
Missing on-chain enforcement of NFT royalty percentage bound (>100%) allows minting of untradeable/bricked NFTs via a raw spend bundle - (File: chia/wallet/nft_wallet/nft_puzzle_utils.py, chia/wallet/nft_wallet/transfer_program_puzzle.py)

### Summary
The `royalty_percentage` value embedded in an NFT's ownership/transfer-program puzzle (curried in basis points, expected range 0–10000) is only validated by the high-level wallet helper `NFTWallet.generate_new_nft()` and the RPC endpoint `nft_mint_nft`. The actual puzzle-construction helpers (`create_ownership_layer_puzzle`, `puzzle_for_transfer_program`) and the underlying CLVM transfer program never enforce this bound. Because NFT minting is just a spend bundle (launcher coin spend + eve coin puzzle), any unprivileged spend-bundle submitter can construct and push a mint transaction directly to the mempool/full node with a `royalty_percentage` curry value far above `10000` (up to the `uint16` max of `65535`), completely bypassing the wallet-layer check.

### Finding Description
`create_ownership_layer_puzzle()` curries the raw `percentage` parameter into `NFT_TRANSFER_PROGRAM_DEFAULT` with no range check: [1](#0-0) 

Likewise `puzzle_for_transfer_program()` curries `percentage` directly with no validation: [2](#0-1) 

The only place this is checked is the convenience wrapper `NFTWallet.generate_new_nft()`: [3](#0-2) 

and the RPC endpoint `nft_mint_nft`, which even more narrowly only rejects the exact value `10000`: [4](#0-3) 

None of these checks are enforced by the CLVM puzzle itself (`NFT_OWNERSHIP_TRANSFER_PROGRAM_ONE_WAY_CLAIM_WITH_ROYALTIES`, loaded as a precompiled program) or by mempool/full-node consensus rules, since NFT semantics are not full-node-validated — only generic CLVM execution and coin conservation are checked. A spend bundle that constructs the launcher + eve-coin spend directly (as `generate_new_nft` itself does at the CoinSpend level) can curry any `uint16` value into the transfer program's royalty percentage, since the puzzle hash simply becomes part of the NFT's on-chain identity: [5](#0-4) 

Once such an NFT exists on-chain, any honest wallet that later tries to use it in a DID-approved trade or an offer will decode the curried percentage via `UncurriedNFT.uncurry()`: [6](#0-5) 

and feed it into `compute_royalty_amount()`, which explicitly rejects percentages above `MAX_ROYALTY_BASIS_POINTS` (10000): [7](#0-6) 

This is exactly analogous to the reported Solidity bug: the "update" path (`generate_new_nft`/`nft_mint_nft`, analogous to `updateConfig()`) enforces the ≤100% invariant, but the underlying puzzle-construction/"init" path (`create_ownership_layer_puzzle`/`puzzle_for_transfer_program`, analogous to `initialize()`) does not, and is directly reachable by an unprivileged spend-bundle submitter.

### Impact Explanation
Any NFT minted on-chain (via a hand-crafted spend bundle rather than the standard wallet RPC) with a royalty percentage above 10000 basis points becomes effectively unusable in the standard trading/royalty-enforcement flow: every honest wallet that tries to include it in an offer or DID-approved trade will raise `ValueError` in `compute_royalty_amount`/`generate_new_nft`, permanently halting the offer-creation/settlement transaction for that NFT. This mirrors the original report's impact ("all sales... will revert") — the malformed asset is bricked with respect to royalty-aware marketplaces/wallets built on this code, and any third-party tooling that doesn't defensively re-validate the on-chain percentage before computing payouts could compute incorrect (over-100%) royalty demands, leading to failed or economically broken settlement spends.

### Likelihood Explanation
Likelihood is Medium: constructing the raw launcher + eve-coin spend bundle with an out-of-range curried percentage requires only building a valid CLVM puzzle/solution and access to a single coin — the same primitives `generate_new_nft` itself uses — with no special privilege, DID ownership, or consensus rule to prevent it. This does not require any peer/node compromise; it's a standard "unprivileged spend-bundle submitter" action.

### Recommendation
Move the `percentage <= MAX_ROYALTY_BASIS_POINTS` validation (and the general `uint16` bound check currently only in `NFTWallet.generate_new_nft`) into the shared puzzle-construction helpers (`create_ownership_layer_puzzle` in `chia/wallet/nft_wallet/nft_puzzle_utils.py` and `puzzle_for_transfer_program` in `chia/wallet/nft_wallet/transfer_program_puzzle.py`), and fix `nft_mint_nft` in `chia/wallet/wallet_rpc_api.py` to reject any `royalty_percentage >= 10000` rather than only the exact value `10000`, so that no code path constructing an NFT ownership/transfer puzzle can produce an out-of-range royalty percentage.

### Proof of Concept
1. A user builds a launcher-coin spend and eve-coin spend for a new NFT exactly as `NFTWallet.generate_new_nft` does (`chia/wallet/nft_wallet/nft_wallet.py:490-544`), but instead of calling `generate_new_nft` (which validates the percentage), directly calls `create_ownership_layer_puzzle(nft_id, did_id, p2_puzzle, percentage=20000, royalty_puzzle_hash=...)` from `chia/wallet/nft_wallet/nft_puzzle_utils.py:188-211` to build the eve coin's puzzle, with `percentage=20000` (200%).
2. The resulting `eve_fullpuz`/launcher spend bundle is signed and pushed directly to a full node/mempool; nothing in consensus or CLVM validation of the NFT puzzle rejects `percentage > 10000`.
3. The NFT is now minted on-chain with royalty percentage 200%.
4. Any wallet later attempting to offer/trade this NFT calls `NFTWallet.royalty_calculation`/`compute_royalty_amount` (`chia/wallet/nft_wallet/nft_wallet.py:64-75`, `839-935`), which raises `ValueError: ... exceeds 100% ...`, permanently blocking creation/completion of any standard offer involving this NFT.

### Citations

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L188-211)
```python
def create_ownership_layer_puzzle(
    nft_id: bytes32,
    did_id: bytes,
    p2_puzzle: Program,
    percentage: uint16,
    royalty_puzzle_hash: bytes32 | None = None,
) -> Program:
    log.debug(
        "Creating ownership layer puzzle with NFT_ID: %s DID_ID: %s Royalty_Percentage: %d P2_puzzle: %s",
        nft_id.hex(),
        did_id,
        percentage,
        p2_puzzle,
    )
    singleton_struct = Program.to((SINGLETON_TOP_LAYER_MOD_HASH, (nft_id, SINGLETON_LAUNCHER_PUZZLE_HASH)))
    if not royalty_puzzle_hash:
        royalty_puzzle_hash = p2_puzzle.get_tree_hash()
    transfer_program = NFT_TRANSFER_PROGRAM_DEFAULT.curry(singleton_struct, royalty_puzzle_hash, percentage)
    nft_inner_puzzle = p2_puzzle

    nft_ownership_layer_puzzle = construct_ownership_layer(
        bytes32(did_id) if did_id else None, transfer_program, nft_inner_puzzle
    )
    return nft_ownership_layer_puzzle
```

**File:** chia/wallet/nft_wallet/transfer_program_puzzle.py (L22-28)
```python
def puzzle_for_transfer_program(launcher_id: bytes32, royalty_puzzle_hash: bytes32, percentage: uint16) -> Program:
    singleton_struct = Program.to((SINGLETON_MOD_HASH, (launcher_id, SINGLETON_LAUNCHER_HASH)))
    return NFT_TRANSFER_PROGRAM_DEFAULT.curry(
        singleton_struct,
        royalty_puzzle_hash,
        percentage,
    )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L64-75)
```python
MAX_ROYALTY_BASIS_POINTS = 10000


def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    """Compute royalty using integer arithmetic, validating against overflow and excessive percentage."""
    if percentage > MAX_ROYALTY_BASIS_POINTS:
        raise ValueError(f"NFT royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
    royalty = uint64(amount)
    if royalty >= abs(offered_amount):
        raise ValueError("Royalty amount meets or exceeds the offered amount")
    return royalty
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L482-489)
```python
        amount = uint64(1)
        # ensure percentage is uint16 and at most 100%
        try:
            percentage = uint16(percentage)
        except ValueError:
            raise ValueError(f"Percentage must be between 0 and {MAX_ROYALTY_BASIS_POINTS} (100%)")
        if percentage > MAX_ROYALTY_BASIS_POINTS:
            raise ValueError(f"Royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L490-544)
```python
        coins = await self.standard_wallet.select_coins(uint64(amount + fee), action_scope)
        origin = coins.copy().pop()
        genesis_launcher_puz = SINGLETON_LAUNCHER_PUZZLE
        # nft_id == singleton_id == launcher_id == launcher_coin.name()
        launcher_coin = Coin(origin.name(), SINGLETON_LAUNCHER_PUZZLE_HASH, uint64(amount))
        self.log.debug("Generating NFT with launcher coin %s and metadata: %s", launcher_coin, metadata)

        p2_inner_puzzle = await action_scope.get_puzzle(self.wallet_state_manager)
        if not target_puzzle_hash:
            target_puzzle_hash = p2_inner_puzzle.get_tree_hash()
        self.log.debug("Attempt to generate a new NFT to %s", target_puzzle_hash.hex())
        if did_id is not None:
            self.log.debug("Creating provenant NFT")
            # eve coin DID can be set to whatever so we keep it empty
            # WARNING: wallets should always ignore DID value for eve coins as they can be set
            #          to any DID without approval
            inner_puzzle = create_ownership_layer_puzzle(
                launcher_coin.name(), b"", p2_inner_puzzle, percentage, royalty_puzzle_hash=royalty_puzzle_hash
            )
            self.log.debug("Got back ownership inner puzzle: %s", inner_puzzle)
        else:
            self.log.debug("Creating standard NFT")
            inner_puzzle = p2_inner_puzzle

        # singleton eve puzzle
        eve_fullpuz = nft_puzzle_utils.create_full_puzzle(
            launcher_coin.name(), metadata, NFT_METADATA_UPDATER.get_tree_hash(), inner_puzzle
        )
        eve_fullpuz_hash = eve_fullpuz.get_tree_hash()
        # launcher announcement
        announcement_message = Program.to([eve_fullpuz_hash, amount, []]).get_tree_hash()

        self.log.debug(
            "Creating transaction for launcher: %s and other coins: %s (%s)", origin, coins, announcement_message
        )
        # store the launcher transaction in the wallet state
        await self.standard_wallet.generate_signed_transaction(
            [uint64(amount)],
            [SINGLETON_LAUNCHER_PUZZLE_HASH],
            action_scope,
            fee,
            coins=coins,
            origin_id=origin.name(),
            extra_conditions=(
                *extra_conditions,
                AssertCoinAnnouncement(asserted_id=launcher_coin.name(), asserted_msg=announcement_message),
            ),
        )
        genesis_launcher_solution = Program.to([eve_fullpuz_hash, amount, []])

        # launcher spend to generate the singleton
        launcher_cs = make_spend(launcher_coin, genesis_launcher_puz, genesis_launcher_solution)
        launcher_sb = WalletSpendBundle([launcher_cs], AugSchemeMPL.aggregate([]))

        eve_coin = Coin(launcher_coin.name(), eve_fullpuz_hash, uint64(amount))
```

**File:** chia/wallet/wallet_rpc_api.py (L2377-2379)
```python
        nft_wallet = self.service.wallet_state_manager.get_wallet(id=request.wallet_id, required_type=NFTWallet)
        if request.royalty_percentage == 10000:
            raise ValueError("Royalty percentage cannot be 100%")
```

**File:** chia/wallet/nft_wallet/uncurry_nft.py (L148-157)
```python
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
```
