### Title
Singleton launcher coins are permissionless — an attacker can front-run the eve spend and hijack a DID/NFT/VC/Data Layer identity before its owner's first revision confirms - (File: chia/wallet/puzzles/singleton_top_layer.py)

### Summary
The Chia singleton protocol splits identity creation into two steps: (1) creating a `SINGLETON_LAUNCHER` coin whose name is the deterministic, publicly computable `launcher_id` (the future DID/NFT/VC/mirror/plotnft/Data-Layer-store identity), and (2) the "eve spend" of that launcher coin, which is the *only* thing that actually binds an inner puzzle (i.e. an owner) to that identity. The `SINGLETON_LAUNCHER` puzzle itself performs no signature or authorization check — whoever gets a spend of the launcher coin into a block first wins the identity, permanently. Chia wallets currently rely purely on client-side convention (bundling the launcher creation and the eve spend into one merged `WalletSpendBundle`) to make this atomic; nothing at the puzzle/consensus level enforces that. If the two steps are ever separated — a crashed/restarted wallet, a manually assembled spend bundle, external tooling, a `merge_spends=False` flow, or two transactions landing in different blocks — the confirmed, unspent launcher coin sits on chain, publicly identifiable (fixed `SINGLETON_LAUNCHER_PUZZLE_HASH`) and spendable by anyone. This exactly mirrors the analog bug class in the report: an ID is generated (the launcher coin/`launcher_id`) before the "first revision" (the eve spend) is created, and during that window any other party can claim ownership of the known identity.

### Finding Description
`launch_conditions_and_coinsol()` in `chia/wallet/puzzles/singleton_top_layer.py` computes the launcher coin deterministically from the parent coin and amount: [1](#0-0) 
and creates the launcher spend with `SINGLETON_LAUNCHER` as puzzle reveal: [2](#0-1) 

The launcher puzzle only checks that the solution's declared full puzzle hash/amount is honored via an announcement assertion made by the *funding* coin — it does not check who is allowed to provide that solution. Anyone who observes the confirmed launcher coin (trivially detectable since `SINGLETON_LAUNCHER_HASH`/`SINGLETON_LAUNCHER_PUZZLE_HASH` is a fixed, global constant used identically by DID, NFT, VC, Data Layer, and pool/plotnft singletons) can spend it with an arbitrary solution and thereby become the "eve" owner of that `launcher_id`.

This pattern is used identically across every singleton-based asset in the wallet:
- DID: `DIDWallet.generate_new_decentralised_id` [3](#0-2) 
- NFT: `NFTWallet.generate_new_nft` [4](#0-3) 
- Data Layer: `DataLayerWallet.generate_new_reporter` [5](#0-4) 
- VC: `VerifiedCredential.launch` [6](#0-5) 

In each case the identity (`launcher_id`) is fixed and published before the eve spend establishes the true owning inner puzzle. The only reason this is safe today is that `WalletStateManager.add_pending_transactions()` aggregates every staged transaction's spend bundle plus any `extra_spends` (which is where the launcher spend is stashed) into a *single* `WalletSpendBundle` when `merge_spends=True` (the default) before pushing: [7](#0-6) 
This is purely a client-side, non-consensus convention. `merge_spends=False` is an explicitly supported code path (used and tested elsewhere in the wallet), and nothing in the singleton puzzle or in mempool/consensus rules prevents the launcher-creation transaction and the eve-spend transaction from being confirmed in different blocks if a caller (a script, an alternate wallet implementation, an exchange integration, or a crashed/retried wallet) submits them separately.

### Impact Explanation
If the eve spend is not confirmed atomically with the launcher coin's creation, any observer who is faster or has better fee/priority can submit a competing eve spend of the exposed launcher coin with their own inner puzzle. Because a singleton's identity thereafter is permanently the `launcher_id`, this is not a recoverable race — whichever eve spend confirms first irrevocably becomes "the" DID/NFT/VC/Data Layer store for that `launcher_id`. Consequences include: theft of a DID identity that a victim already began minting and may have referenced elsewhere, hijacking of an NFT mint (forging the on-chain identity a marketplace/buyer expects), hijacking of a Data Layer store's `launcher_id` (breaking Data Layer root/proof trust), or claiming a VC intended for a specific proof provider. This is a forged-asset-identity / unauthorized-ownership-claim impact directly analogous to the Juju secret-ownership hijack.

### Likelihood Explanation
Exploitation requires only passive chain observation (watching for confirmed, unspent coins with `puzzle_hash == SINGLETON_LAUNCHER_PUZZLE_HASH`/`SINGLETON_LAUNCHER_HASH`) plus the ability to submit a spend bundle — no special privilege, signature, or key is needed since the launcher puzzle is unauthenticated by design. The window only opens when launcher creation and eve spend are not bundled atomically at push time; the default wallet path defends against this by convention (`merge_spends=True`), but this is not a protocol-enforced guarantee, and multiple explicitly-supported code paths (`merge_spends=False`, manual spend-bundle construction, external tooling, split RPC calls, restart-after-partial-broadcast) can produce the unprotected window in practice.

### Recommendation
- Do not rely solely on client-side bundle merging for launcher/eve-spend atomicity. Consider adding a puzzle-level or wallet-level commitment (e.g., requiring the launcher solution to include a hash lock/pubkey commitment tied to the originating coin, or enforcing at the RPC/API boundary that launcher creation and eve spend can never be split into independently-pushable transactions).
- Audit and restrict `merge_spends=False` usage and any code path (DID/NFT/VC/DL wallet, plotnft/pool wallet) that could push a launcher-creating transaction without its corresponding eve spend in the same aggregated bundle.
- Add wallet-level detection/alerting for "orphaned" launcher coins (confirmed and unspent `SINGLETON_LAUNCHER` coins with no matching eve spend within N blocks) so users/services can react before an attacker claims the identity.
- Document explicitly, for any third-party/alternate wallet implementations, that launcher creation and eve spend MUST be included in the same spend bundle to be safe.

### Proof of Concept
1. Alice calls (conceptually) the DID/NFT/DataLayer flow, but her wallet only manages to broadcast the *first* transaction (the funding-coin spend that creates the `SINGLETON_LAUNCHER` coin with `SINGLETON_LAUNCHER_PUZZLE_HASH`), e.g. due to a crash between `generate_signed_transaction(...)` and the aggregation/push of `launcher_sb`/`eve_spend` in `generate_new_decentralised_id`/`generate_new_nft`/`generate_new_reporter` (`chia/wallet/did_wallet/did_wallet.py:1076-1122`, `chia/wallet/nft_wallet/nft_wallet.py:526-547`, `chia/data_layer/data_layer_wallet.py:327-346`), or because a script/service pushes the two `TransactionRecord`s independently with `merge_spends=False`.
2. This first transaction confirms on-chain, creating an unspent coin `Coin(parent.name(), SINGLETON_LAUNCHER_HASH, amount)` — the `launcher_id` is now fixed and publicly known (it's simply that coin's `.name()`).
3. Mallory watches the mempool/chain for coins whose `puzzle_hash` equals the well-known `SINGLETON_LAUNCHER_HASH`/`SINGLETON_LAUNCHER_PUZZLE_HASH` constant and finds Alice's unspent launcher coin before Alice's wallet retries/broadcasts the eve spend.
4. Mallory constructs her own spend of that launcher coin using `singleton_top_layer.puzzle_for_singleton(launcher_id, mallory_inner_puzzle)` and a `genesis_launcher_solution`/`launcher_solution` of her choosing (no signature required by `SINGLETON_LAUNCHER`), and gets it confirmed first.
5. The singleton with that `launcher_id` now runs Mallory's inner puzzle as the eve coin; Alice's original eve spend (which asserts `ASSERT_MY_COIN_ID`/depends on the launcher coin still existing) becomes permanently invalid, and Mallory now controls the DID/NFT/VC/Data-Layer-store identity that was meant to be Alice's.

### Citations

**File:** chia/wallet/puzzles/singleton_top_layer.py (L187-189)
```python
# Given the parent and amount of the launcher coin, return the launcher coin
def generate_launcher_coin(coin: Coin, amount: uint64) -> Coin:
    return Coin(coin.name(), SINGLETON_LAUNCHER_HASH, amount)
```

**File:** chia/wallet/puzzles/singleton_top_layer.py (L203-225)
```python
# Take standard coin and amount -> launch conditions & launcher coin solution
def launch_conditions_and_coinsol(
    coin: Coin, inner_puzzle: Program, comment: list[tuple[str, str]], amount: uint64
) -> tuple[list[Program], CoinSpend]:
    if (amount % 2) == 0:
        raise ValueError("Coin amount cannot be even. Subtract one mojo.")

    launcher_coin = generate_launcher_coin(coin, amount)
    curried_singleton = SINGLETON_MOD.curry(
        (SINGLETON_MOD_HASH, (launcher_coin.name(), SINGLETON_LAUNCHER_HASH)), inner_puzzle
    )

    launcher_solution = Program.to([curried_singleton.get_tree_hash(), amount, comment])
    create_launcher = Program.to([ConditionOpcode.CREATE_COIN, SINGLETON_LAUNCHER_HASH, amount])
    assert_launcher_announcement = Program.to(
        [ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT, std_hash(launcher_coin.name() + launcher_solution.get_tree_hash())]
    )

    conditions = [create_launcher, assert_launcher_announcement]

    launcher_coin_spend = make_spend(launcher_coin, SINGLETON_LAUNCHER, launcher_solution)

    return conditions, launcher_coin_spend
```

**File:** chia/wallet/did_wallet/did_wallet.py (L1065-1093)
```python
        origin = coins.copy().pop()
        genesis_launcher_puz = SINGLETON_LAUNCHER_PUZZLE
        launcher_coin = Coin(origin.name(), genesis_launcher_puz.get_tree_hash(), amount)

        did_inner: Program = await self.get_did_innerpuz(action_scope, origin_id=launcher_coin.name())
        did_inner_hash = did_inner.get_tree_hash()
        did_full_puz = create_singleton_puzzle(did_inner, launcher_coin.name())
        did_puzzle_hash = did_full_puz.get_tree_hash()

        announcement_message = Program.to([did_puzzle_hash, amount, bytes(0x80)]).get_tree_hash()

        await self.standard_wallet.generate_signed_transaction(
            amounts=[amount],
            puzzle_hashes=[genesis_launcher_puz.get_tree_hash()],
            action_scope=action_scope,
            fee=fee,
            coins=coins,
            origin_id=origin.name(),
            extra_conditions=(
                AssertCoinAnnouncement(asserted_id=launcher_coin.name(), asserted_msg=announcement_message),
                *extra_conditions,
            ),
        )

        genesis_launcher_solution = Program.to([did_puzzle_hash, amount, bytes(0x80)])

        launcher_cs = make_spend(launcher_coin, genesis_launcher_puz, genesis_launcher_solution)
        launcher_sb = WalletSpendBundle([launcher_cs], AugSchemeMPL.aggregate([]))
        eve_coin = Coin(launcher_coin.name(), did_puzzle_hash, amount)
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

**File:** chia/data_layer/data_layer_wallet.py (L311-343)
```python
        coins: set[Coin] = await self.standard_wallet.select_coins(uint64(fee + 1), action_scope)
        if coins is None:
            raise ValueError("Not enough coins to create new data layer singleton")

        launcher_parent: Coin = next(iter(coins))
        launcher_coin: Coin = Coin(launcher_parent.name(), SINGLETON_LAUNCHER_PUZZLE_HASH, uint64(1))

        inner_puzzle: Program = await action_scope.get_puzzle(self.wallet_state_manager)
        full_puzzle: Program = create_host_fullpuz(inner_puzzle, initial_root, launcher_coin.name())

        genesis_launcher_solution: Program = Program.to(
            [full_puzzle.get_tree_hash(), 1, [initial_root, inner_puzzle.get_tree_hash()]]
        )
        announcement_message: bytes32 = genesis_launcher_solution.get_tree_hash()
        announcement = AssertCoinAnnouncement(asserted_id=launcher_coin.name(), asserted_msg=announcement_message)

        await self.standard_wallet.generate_signed_transaction(
            amounts=[uint64(1)],
            puzzle_hashes=[SINGLETON_LAUNCHER_PUZZLE_HASH],
            action_scope=action_scope,
            fee=fee,
            origin_id=launcher_parent.name(),
            coins=coins,
            extra_conditions=(*extra_conditions, announcement),
        )

        launcher_cs: CoinSpend = make_spend(
            launcher_coin,
            SINGLETON_LAUNCHER_PUZZLE,
            genesis_launcher_solution,
        )
        launcher_sb = WalletSpendBundle([launcher_cs], G2Element())
        launcher_id = launcher_coin.name()
```

**File:** chia/wallet/vc_wallet/vc_drivers.py (L340-392)
```python
        origin_coin = origin_coins[0]
        launcher_coin: Coin = generate_launcher_coin(origin_coin, uint64(1))

        # Create the second puzzle for the first launch
        curried_eve_singleton: Program = puzzle_for_singleton(
            launcher_coin.name(),
            OWNERSHIP_LAYER_LAUNCHER,
        )
        curried_eve_singleton_hash: bytes32 = curried_eve_singleton.get_tree_hash()
        launcher_solution = Program.to([curried_eve_singleton_hash, uint64(1), None])

        # Create the final puzzle for the second launch
        inner_transfer_program: Program = create_did_tp()
        transfer_program: Program = create_tp_covenant_adapter(
            create_covenant_layer(
                curried_eve_singleton_hash,
                create_eml_covenant_morpher(
                    inner_transfer_program.get_tree_hash(),
                ),
                inner_transfer_program,
            )
        )
        wrapped_inner_puzzle_hash: bytes32 = create_revocation_layer(
            STANDARD_BRICK_PUZZLE_HASH,
            new_inner_puzzle_hash,
        ).get_tree_hash()
        metadata_layer_hash: bytes32 = construct_exigent_metadata_layer(
            Program.to((provider_id, None)),
            transfer_program,
            wrapped_inner_puzzle_hash,  # type: ignore
        ).get_tree_hash_precalc(wrapped_inner_puzzle_hash)
        curried_singleton_hash: bytes32 = puzzle_for_singleton(
            launcher_coin.name(),
            metadata_layer_hash,  # type: ignore
        ).get_tree_hash_precalc(metadata_layer_hash)
        launch_dpuz: Program = Program.to(
            (
                1,
                [
                    [51, wrapped_inner_puzzle_hash, uint64(1), memos],
                    [1, new_inner_puzzle_hash],
                    [-10, provider_id, transfer_program.get_tree_hash()],
                ],
            )
        )
        second_launcher_solution = Program.to([launch_dpuz, None])
        second_launcher_coin: Coin = Coin(
            launcher_coin.name(),
            curried_eve_singleton_hash,
            uint64(1),
        )
        first_launcher_announcement_hash = std_hash(launcher_coin.name() + launcher_solution.get_tree_hash())
        second_launcher_announcement_hash = std_hash(second_launcher_coin.name() + launch_dpuz.get_tree_hash())
```

**File:** chia/wallet/wallet_state_manager.py (L1815-1827)
```python
        agg_spend = WalletSpendBundle.aggregate([tx.spend_bundle for tx in tx_records if tx.spend_bundle is not None])
        if extra_spends is not None:
            agg_spend = WalletSpendBundle.aggregate([agg_spend, *extra_spends])
        actual_spend_involved: bool = agg_spend != WalletSpendBundle([], G2Element())
        if merge_spends and actual_spend_involved:
            tx_records = [
                dataclasses.replace(
                    tx,
                    spend_bundle=agg_spend if i == 0 else None,
                    name=agg_spend.name() if i == 0 else bytes32.secret(),
                )
                for i, tx in enumerate(tx_records)
            ]
```
