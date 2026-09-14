### Title
CR-CAT wallet identity keyed only on TAIL hash while ignoring `authorized_providers`/`proofs_checker` allows a counterparty to squat and corrupt a CR-CAT's tracked configuration - (File: chia/wallet/vc_wallet/cr_cat_wallet.py)

### Summary
The reported Ajna bug is a class of "unique identity computed from only part of the real configuration, first writer wins, later legitimate writers are silently ignored." The same pattern exists in chia's CR-CAT (credential-restricted CAT) wallet: a CR-CAT's true on-chain identity/puzzle depends on `tail_hash` **plus** `authorized_providers` and `proofs_checker` (the CR layer parameters), but `CRCATWallet.get_or_create_wallet_for_cat()` treats `tail_hash` alone as the unique key when deciding whether to reuse an existing wallet.

### Finding Description
`CRCATWallet.get_or_create_wallet_for_cat()` loops over existing wallets and matches solely on `w.get_asset_id() == limitations_program_hash` (the TAIL hash), then silently returns the pre-existing wallet, discarding the caller-supplied `authorized_providers`/`proofs_checker`: [1](#0-0) 

The actual CR-CAT puzzle/coin identity is `construct_cat_puzzle(CAT_MOD, tail_hash, construct_cr_layer(authorized_providers, proofs_checker, inner_ph))`, i.e. `authorized_providers` and `proofs_checker` are just as much part of a CR-CAT's identity as the TAIL, exactly as `interestRate_` is part of a pool's true identity in the Ajna report even though `PoolDeployer.canDeploy()` only keys uniqueness on `(subsetHash_, collateral_, quote_)`: [2](#0-1) 

This same asset_id-only matching is repeated during wallet sync in `CATWallet.identify()`, which looks up an existing `CRCAT` wallet purely by `crcat_info.limitations_program_hash == asset_id`, again ignoring `authorized_providers`/`proofs_checker`: [3](#0-2) 

`get_or_create_wallet_for_cat` is also reachable from `create_from_puzzle_info()`, which is driven by a `PuzzleInfo`/`driver_dict` — attacker/counterparty-supplied metadata used when accepting an `Offer`: [4](#0-3) 

Once a wallet is created (or reused) for a given `tail_hash`, all subsequent CR-CAT coin handling for that `tail_hash` uses the wallet's stored `self.info.authorized_providers`/`self.info.proofs_checker`, not the parameters embedded in the actual on-chain coin, when reconstructing the `CRCAT` object for spending or balance accounting: [5](#0-4) 

Because a "first writer wins" registration keyed only on `tail_hash` locks in whichever `authorized_providers`/`proofs_checker` were supplied first (analogous to the Ajna pool interest-rate front-run), a counterparty can pre-register (via an offer's `driver_dict`, before the victim has ever seen a legitimate CR-CAT of that TAIL) a `CRCATWallet` for a given `tail_hash` with attacker-chosen `authorized_providers`/`proofs_checker`. When the victim later receives a genuinely different, legitimately-configured CR-CAT sharing the same `tail_hash` (a valid scenario since the CR layer/providers are independent of the TAIL commitment), `identify()` will match the wallet by `tail_hash` alone and silently attach the new coin to the pre-existing (attacker-influenced) wallet, whose cached `authorized_providers`/`proofs_checker` do not match the real coin.

### Impact Explanation
Once the wallet's `CRCATInfo` is desynchronized from a coin's real on-chain CR layer parameters, `coin_record_to_crcat()` reconstructs a `CRCAT` with the wrong `authorized_providers`/`proofs_checker`. Spend construction (`_generate_unsigned_spendbundle`, `claim_pending_approval_balance`) uses these wrong parameters both to look up a VC with a matching provider set and to build the CR-layer puzzle reveal/solution. This produces a puzzle reveal whose tree hash does not match the actual on-chain coin puzzle hash, so any spend attempt against that coin is invalid and rejected by the network — effectively bricking the victim's own legitimately-received CR-CAT funds for that asset. This is a Medium-severity coin-lock/DoS on user funds rooted purely in an incomplete identity key (asset_id-only matching) accepted from local sync/offer-driven wallet creation, without requiring any malicious peer or network-layer exploit — it is reachable purely through standard offer acceptance / wallet-sync coin discovery paths available to an unprivileged wallet user or offer counterparty.

### Likelihood Explanation
Reasonably likely wherever CR-CATs (credential-restricted CATs, used for KYC'd/regulated asset flows) with shared TAILs but differing provider/proof configurations are traded via offers, since `driver_dict` metadata embedded in an `Offer` is attacker-controlled input consumed by `create_from_puzzle_info` → `get_or_create_wallet_for_cat` before any legitimate coin of that TAIL has been seen by the victim wallet.

### Recommendation
Include `authorized_providers` and `proofs_checker` (or a hash of the full CR layer configuration) in the uniqueness key used by `CRCATWallet.get_or_create_wallet_for_cat()` and in the `CATWallet.identify()` lookup for `CRCAT` wallets, so that CR-CATs with the same TAIL hash but different CR-layer configurations are tracked by distinct wallets instead of silently reusing/overwriting the first-registered configuration.

### Proof of Concept
Conceptual sequence (not full runnable code, since it requires wiring a real offer-driven sync flow across two wallet nodes):
1. Attacker crafts an `Offer` whose `driver_dict` for asset `tail_hash = T` specifies `authorized_providers = [P_attacker]`, `proofs_checker = C_attacker`.
2. Victim wallet accepts the offer; `create_from_puzzle_info` (cr_cat_wallet.py:133-156) creates a `CRCATWallet` keyed by `T` with `(P_attacker, C_attacker)` via `get_or_create_wallet_for_cat` (cr_cat_wallet.py:98-131).
3. Later, victim independently receives a legitimate CR-CAT coin with the same `tail_hash = T` but real, different `authorized_providers = [P_real]`, `proofs_checker = C_real`.
4. During sync, `CATWallet.identify()` (cat_wallet.py:509-513) matches on `tail_hash` alone and attaches this new coin to the existing wallet still configured with `(P_attacker, C_attacker)`.
5. When the victim attempts to spend this coin, `coin_record_to_crcat()` (cr_cat_wallet.py:370-393) builds the `CRCAT`/puzzle reveal using `(P_attacker, C_attacker)` instead of `(P_real, C_real)`, producing a puzzle reveal whose hash mismatches the coin's actual puzzle hash — the spend is invalid and the coin becomes unspendable through normal wallet flows.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L98-131)
```python
    @classmethod
    async def get_or_create_wallet_for_cat(
        cls,
        wallet_state_manager: WalletStateManager,
        wallet: Wallet,
        limitations_program_hash: bytes32,
        name: str | None = None,
        authorized_providers: list[bytes32] | None = None,
        proofs_checker: ProofsChecker | None = None,
    ) -> Self:
        if authorized_providers is None or proofs_checker is None:  # pragma: no cover
            raise ValueError("get_or_create_wallet_for_cat was call on CRCATWallet without proper arguments")
        self = cls()
        self.standard_wallet = wallet
        if name is None:
            name = self.default_wallet_name_for_unknown_cat(limitations_program_hash)
        self.log = logging.getLogger(name)

        for id, w in wallet_state_manager.wallets.items():
            if w.type() == cls.type():
                assert isinstance(w, cls)
                if w.get_asset_id() == limitations_program_hash:
                    self.log.warning("Not creating wallet for already existing CR-CAT wallet")
                    return w

        self.wallet_state_manager = wallet_state_manager

        self.info = CRCATInfo(limitations_program_hash, None, authorized_providers, proofs_checker)
        info_as_string = bytes(self.info).hex()
        self.wallet_info = await wallet_state_manager.user_store.create_wallet(name, WalletType.CRCAT, info_as_string)

        await self.wallet_state_manager.add_new_wallet(self)
        self.tail_hash = self.info.limitations_program_hash
        return self
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L133-156)
```python
    @classmethod
    async def create_from_puzzle_info(
        cls,
        wallet_state_manager: WalletStateManager,
        wallet: Wallet,
        puzzle_driver: PuzzleInfo,
        name: str | None = None,
        # We're hinting this as Any for mypy by should explore adding this to the wallet protocol and hinting properly
        potential_subclasses: dict[AssetType, Any] | None = None,
    ) -> Any:
        if potential_subclasses is None:
            potential_subclasses = {}

        cr_layer: PuzzleInfo | None = puzzle_driver.also()
        if cr_layer is None:  # pragma: no cover
            raise ValueError("create_from_puzzle_info called on CRCATWallet with a non CR-CAT puzzle driver")
        return await cls.get_or_create_wallet_for_cat(
            wallet_state_manager,
            wallet,
            puzzle_driver["tail"],
            name,
            [bytes32(provider) for provider in cr_layer["authorized_providers"]],
            ProofsChecker.from_program(UnknownPuzzle(known_program=cr_layer["proofs_checker"])),
        )
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L370-393)
```python
    def coin_record_to_crcat(self, coin_record: WalletCoinRecord) -> CRCAT:
        if coin_record.coin_type not in {CoinType.CRCAT, CoinType.CRCAT_PENDING}:  # pragma: no cover
            raise ValueError(f"Attempting to spend a non-CRCAT coin: {coin_record.coin.name().hex()}")
        if coin_record.metadata is None:  # pragma: no cover
            raise ValueError(f"Attempting to spend a CRCAT coin without metadata: {coin_record.coin.name().hex()}")
        try:
            metadata: CRCATMetadata = CRCATWallet.get_metadata_from_record(coin_record)
            crcat: CRCAT = CRCAT(
                coin_record.coin,
                self.info.limitations_program_hash,
                metadata.lineage_proof,
                self.info.authorized_providers,
                self.info.proofs_checker.as_program(),
                (
                    construct_pending_approval_state(
                        metadata.inner_puzzle_hash, uint64(coin_record.coin.amount)
                    ).get_tree_hash()
                    if coin_record.coin_type == CoinType.CRCAT_PENDING
                    else metadata.inner_puzzle_hash
                ),
            )
            return crcat
        except Exception as e:  # pragma: no cover
            raise ValueError(f"Error parsing CRCAT metadata: {e}")
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L200-209)
```python
        tail_hash: bytes32 = tail.get_tree_hash()

        new_cr_layer_hash: bytes32 = construct_cr_layer(
            authorized_providers,
            proofs_checker,
            payment.puzzle_hash,  # type: ignore
        ).get_tree_hash_precalc(payment.puzzle_hash)
        new_cat_puzhash = construct_cat_puzzle(CAT_MOD, tail_hash, new_cr_layer_hash).get_tree_hash_precalc(
            new_cr_layer_hash
        )
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L509-513)
```python
                # Check if we already have a wallet
                for wallet_info in await wallet_state_manager.get_all_wallet_info_entries(wallet_type=WalletType.CRCAT):
                    crcat_info: CRCATInfo = CRCATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    if crcat_info.limitations_program_hash == asset_id:
                        return WalletIdentifier(wallet_info.id, WalletType(wallet_info.type))
```
