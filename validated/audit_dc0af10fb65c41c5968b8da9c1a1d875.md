Based on my analysis, this is the vulnerability class match: an unprivileged attacker can pre-plant a DID coin — hinted to the victim's own already-derived puzzle hash, but with attacker-chosen recovery configuration — and the victim's wallet will *automatically* adopt it as one of "their" DID wallets, without any user action or consent, analogous to Nextcloud Notes silently adopting an attacker-shared `Notes/` folder as the user's own storage before the user ever initialized it themselves.

### Title
Wallet auto-adopts attacker-crafted DID coins as owned identities via unauthenticated hint matching, allowing recovery-based hijack - ([File: chia/wallet/did_wallet/did_wallet.py])

### Summary
`DIDWallet.identify()` and `WalletStateManager.determine_coin_type()`/`_add_coin_state()` automatically create a local DID wallet entry for *any* DID-shaped coin whose puzzle uses the wallet's own already-derived inner puzzle (public information, since a wallet's puzzle hashes/pubkeys can be observed on-chain or simply guessed from previously used addresses), as long as the local `did_auto_add_limit` (default 10) has not been reached. This auto-adoption occurs with **no user consent** and is enabled by default (unlike the analogous CAT auto-add config `automatically_add_unknown_cats`, which defaults to `False`).

### Finding Description
`DIDWallet.identify()` [1](#0-0)  resolves ownership purely from the coin's *hint*, looking up `derivation_record_for_puzzle_hash(hinted_coin.hint)`. Hints are attacker-controlled data embedded in a `CREATE_COIN` condition and are not cryptographically bound to true recipient consent — anyone can hint a coin to any puzzle hash they can observe.

Once a `derivation_record` match is found, the code reconstructs the DID puzzle using the *victim's* real inner puzzle but with **attacker-supplied** `recovery_list_hash`, `num_verification`, and `metadata` taken directly from the malicious `coin_spend`'s curried arguments [2](#0-1) . Since the full puzzle hash is deterministically derived from these parameters plus the victim's real inner puzzle, an attacker who knows the victim's puzzle hash can construct a puzzle reveal that "matches", giving them full control over the recovery configuration of a DID which will now be added to the victim's wallet.

If the auto-add count is under the configured limit, the wallet silently creates a new local `DIDWallet` for this attacker-crafted coin via `DIDWallet.create_new_did_wallet_from_coin_spend()` and fires a `wallet_created` websocket event [3](#0-2) . This happens automatically during normal coin-state sync, with no explicit user action, and the config comment confirms this is opt-in-by-default rather than opt-in-by-choice, unlike the CAT auto-add feature: [4](#0-3) .

Because the attacker fully controls `recovery_list_hash`/`num_verification` (i.e., which DIDs can execute a "lost key" recovery for this identity) while the victim controls only the p2 inner spend key, the victim has no way to know, from the wallet UI alone, that a "found" DID they now see in their wallet has a backdoored recovery configuration set by a third party — directly mirroring the Nextcloud Notes bug class where a resource pre-planted by an attacker is silently and automatically treated as belonging to the legitimate user before they ever explicitly created or reviewed it.

### Impact Explanation
If a victim is subsequently induced to use this auto-discovered DID as their real identity (e.g., minting/receiving NFTs bound to it, or accepting offers/approvals gated on this DID), the attacker — as a configured backup/recovery ID — can later execute a DID recovery spend using `did_wallet_puzzles.create_recovery_message_puzzle()`/the recovery attestation flow to transfer control of the DID singleton (and by extension coins, NFTs, or CAT approvals scoped to that identity) to an attacker-controlled key, without any signature from the victim. This is an unauthorized coin/identity-ownership takeover reachable purely by a single crafted coin spend visible to the victim's syncing wallet — no privileged network position or victim credentials required.

### Likelihood Explanation
Exploitation requires the attacker to know (or guess) one of the victim's already-used or externally-known puzzle hashes (e.g., an address they've published to receive a payment), which is routine information exposure for any wallet user, and then send/self-spend a low-value coin crafted as a DID pointing to that hash. `did_auto_add_limit` defaults to 10, so the auto-add path is enabled out of the box, and the victim need take no explicit action for the malicious wallet entry to appear.

### Recommendation
Do not auto-materialize a fully independent `DIDWallet` (with attacker-chosen recovery configuration) purely from an on-chain hint match. At minimum, require explicit user confirmation before accepting an auto-discovered DID's recovery/backup configuration, or clearly and prominently surface the recovery list/verification threshold as untrusted/attacker-controlled metadata in any auto-add notification, matching the explicit opt-in warning already used for `automatically_add_unknown_cats`.

### Proof of Concept
1. Attacker observes a puzzle hash `PH` previously used/derived by victim's wallet (e.g., a payment address the victim shared).
2. Attacker crafts a DID launcher + eve spend using `did_wallet_puzzles.create_innerpuz(p2_puzzle_or_hash=PH, recovery_list=[attacker_did], num_of_backup_ids_needed=1, launcher_id=..., metadata=...)`, hinting the child coin to `PH`.
3. Attacker broadcasts this spend on-chain (any low-value XCH suffices, oddness constraint for singleton amount).
4. Victim's wallet syncs, `determine_coin_type` → `DIDWallet.identify()` matches `PH`'s derivation record, reconstructs the full puzzle using the attacker's `recovery_list_hash`, and (since `did_wallet_count < did_auto_add_limit`) silently creates a new local DID wallet entry owned, from the recovery perspective, by the attacker.
5. Victim starts using this DID (mints NFTs, approves credentials, etc.).
6. Attacker later performs a recovery spend as the sole backup ID, seizing control of the DID singleton and any linked assets.

### Citations

**File:** chia/wallet/did_wallet/did_wallet.py (L438-467)
```python
    @classmethod
    async def identify(
        cls,
        wallet_state_manager: WalletStateManager,
        sync_scope: WalletSyncScope,
        parent_data: DIDCoinData,
        parent_coin_state: CoinState,
        coin_state: CoinState,
        coin_spend: CoinSpend,
        peer: WSChiaConnection,
    ) -> WalletIdentifier | None:
        """
        Handle the new coin when it is a DID
        :param parent_data: Curried data of the DID coin
        :param parent_coin_state: Parent coin state
        :param coin_state: Current coin state
        :param coin_spend: New coin spend
        :return: Wallet ID & Wallet Type
        """

        inner_puzzle_hash = parent_data.p2_puzzle.get_tree_hash()
        wallet_state_manager.log.info(
            f"parent: {parent_coin_state.coin.name()} inner_puzzle_hash for parent is {inner_puzzle_hash}"
        )

        hinted_coin = compute_spend_hints_and_additions(coin_spend)[0][coin_state.coin.name()]
        assert hinted_coin.hint is not None, f"hint missing for coin {hinted_coin.coin}"
        derivation_record = await wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
            hinted_coin.hint
        )
```

**File:** chia/wallet/did_wallet/did_wallet.py (L493-531)
```python
        else:
            our_inner_puzzle: Program = wallet_state_manager.main_wallet.puzzle_for_pk(derivation_record.pubkey)

            wallet_state_manager.log.info(f"Found DID, launch_id {launch_id}.")
            did_puzzle = did_wallet_puzzles.DID_INNERPUZ_MOD.curry(
                our_inner_puzzle,
                parent_data.recovery_list_hash,
                parent_data.num_verification,
                parent_data.singleton_struct,
                parent_data.metadata,
            )
            full_puzzle = create_singleton_puzzle(did_puzzle, launch_id)
            did_puzzle_empty_recovery = did_wallet_puzzles.DID_INNERPUZ_MOD.curry(
                our_inner_puzzle,
                NIL_TREEHASH,
                uint64(0),
                parent_data.singleton_struct,
                parent_data.metadata,
            )
            alt_did_puzzle_empty_recovery = did_wallet_puzzles.DID_INNERPUZ_MOD.curry(
                our_inner_puzzle,
                Program.NIL,
                uint64(0),
                parent_data.singleton_struct,
                parent_data.metadata,
            )

            full_puzzle_empty_recovery = create_singleton_puzzle(did_puzzle_empty_recovery, launch_id)
            alt_full_puzzle_empty_recovery = create_singleton_puzzle(alt_did_puzzle_empty_recovery, launch_id)
            if full_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                if full_puzzle_empty_recovery.get_tree_hash() == coin_state.coin.puzzle_hash:
                    did_puzzle = did_puzzle_empty_recovery
                    wallet_state_manager.log.info("DID recovery list was reset by the previous owner.")
                elif alt_full_puzzle_empty_recovery.get_tree_hash() == coin_state.coin.puzzle_hash:
                    did_puzzle = alt_did_puzzle_empty_recovery
                    wallet_state_manager.log.info("DID recovery list was reset by the previous owner.")
                else:
                    wallet_state_manager.log.error("DID puzzle hash doesn't match, please check curried parameters.")
                    return None
```

**File:** chia/wallet/did_wallet/did_wallet.py (L552-577)
```python
            # check we aren't above the auto-add wallet limit
            limit = wallet_state_manager.config.get("did_auto_add_limit", 10)
            if did_wallet_count < limit:
                did_wallet = await DIDWallet.create_new_did_wallet_from_coin_spend(
                    wallet_state_manager,
                    wallet_state_manager.main_wallet,
                    launch_coin.coin,
                    did_puzzle,
                    coin_spend,
                    f"DID {encode_puzzle_hash(launch_id, AddressType.DID.hrp(wallet_state_manager.config))}",
                )
                wallet_identifier = WalletIdentifier.create(did_wallet)
                async with sync_scope.use() as interface:
                    interface.side_effects.websocket_events.append(
                        WebSocketEvent(
                            name="wallet_created",
                            wallet_id=wallet_identifier.id,
                            data={"did_id": did_wallet.get_my_DID()},
                        )
                    )
                return wallet_identifier
            # we are over the limit
            wallet_state_manager.log.warning(
                f"You are at the max configured limit of {limit} DIDs. Ignoring received DID {launch_id.hex()}"
            )
            return None
```

**File:** chia/util/initial-config.yaml (L587-588)
```yaml
  # if an unknown DID is sent to us, a wallet will be automatically created
  did_auto_add_limit: 10
```
