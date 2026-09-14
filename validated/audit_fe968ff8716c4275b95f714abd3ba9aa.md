### Title
Unauthenticated CR-CAT coin with a matching TAIL hash silently converts an existing CATWallet, corrupting spendability of already-held funds - (File: `chia/wallet/vc_wallet/cr_cat_wallet.py`)

### Summary
`CATWallet.identify()` automatically converts an existing, already-funded `CATWallet` into a `CRCATWallet` the moment it observes *any* incoming coin whose TAIL (asset id) matches, wrapped in a credential-restriction ("CR") layer with attacker-chosen `authorized_providers`/`proofs_checker`. Unlike the sibling `RCATWallet.convert_to_revocable()` path, `CRCATWallet.convert_to_cr()` performs **no check** that the existing CAT wallet is empty of prior, unrestricted CAT coins before hijacking its wallet-info record and re-typing it. This is the same bug class as Pickle Finance's `swapExactJarForJar`: an operation that repoints/redefines a victim's existing vault state onto attacker-supplied parameters, with no whitelist/ownership check that the destination (here, the "authority" governing future spends) is legitimate.

### Finding Description
In `chia/wallet/cat_wallet/cat_wallet.py`, `identify()` handles a newly-seen coin state. If the coin turns out to be a CR-CAT (credential-restricted CAT) sharing a TAIL hash with an existing plain `CATWallet` the local wallet already tracks, it does: [1](#0-0) 

This calls `CRCATWallet.convert_to_cr(found_cat_wallet, crcat.authorized_providers, ...)` unconditionally, where `crcat.authorized_providers` and `crcat.proofs_checker` are parsed directly out of the attacker-controlled coin spend that was just observed on chain (`CRCAT.get_next_from_coin_spend`): [2](#0-1) 

`convert_to_cr` immediately overwrites the wallet-info row (`user_store.update_wallet`) and replaces the in-memory wallet object (`cat_wallet.wallet_state_manager.wallets[cat_wallet.id()] = replace_self`) with a `CRCATWallet` bound to the attacker-supplied `authorized_providers`/`proofs_checker` — with **no guard on whether the CAT wallet already holds funds** minted/received under the unrestricted CAT puzzle.

Contrast this with the parallel R-CAT conversion path, which explicitly guards against exactly this scenario: [3](#0-2) 

`convert_to_revocable` refuses to convert if `cat_wallet.lineage_store.is_empty()` is false (i.e., the wallet already tracks CAT coins). `convert_to_cr` has no equivalent check, so any coin-set state (including a dust CR-CAT payment an attacker sends to a puzzle hash the victim's wallet watches) with the same TAIL as a CAT the victim already holds real value in will retype the victim's wallet.

Once converted, every future spend of the pre-existing (still plain `CAT_MOD`-curried, unmodified on-chain) coins goes through `CRCATWallet`'s puzzle construction/solving logic, which curries in the credential-restriction layer and requires a VC satisfying attacker-chosen `authorized_providers`/`proofs_checker` (see `CRCATWallet.get_puzzle_info` / `match_puzzle_info`): [4](#0-3) 

Because the real on-chain coins were never actually wrapped in a CR layer, the wallet's reconstructed puzzle reveal will mismatch the true on-chain puzzle hash, and/or the wallet will refuse to build a spend without a VC from the attacker's fabricated `authorized_providers` — the victim can no longer move funds that are rightfully theirs through the wallet software.

### Impact Explanation
This is a spend-triggered transaction-processing halt on the victim's own, previously-spendable CAT balance: an unprivileged party can force the victim's wallet to misclassify and effectively lock the coins it already owns by broadcasting a coin sharing the victim's TAIL hash wrapped in a CR layer with bogus authorization parameters. TAIL hashes for many CATs (e.g., `genesis_by_id`/`genesis_by_puzhash` derived from public coin data, or any custom/public CAT) are discoverable/public, so no privileged access or key leak is required — only the ability to get a coin observed by the victim's node/wallet sync, i.e., a standard, unprivileged spend-bundle submission.

### Likelihood Explanation
Medium-High: the attacker only needs to mint (or reuse) a CR-CAT coin whose TAIL equals a target CAT's TAIL and send an arbitrarily small amount of it in a way the victim's wallet will sync (e.g., hinted to the victim's puzzle hash, or simply because the victim's wallet tracks CAT wallets by asset id irrespective of destination). No signature from the victim, no cooperation, and no special permissions are needed — this is reachable purely through normal wallet sync of on-chain coin states following a submitted spend bundle.

### Recommendation
Add the same protective check used in `RCATWallet.convert_to_revocable()` to `CRCATWallet.convert_to_cr()`: refuse (or require explicit user confirmation) to convert an existing `CATWallet` when `cat_wallet.lineage_store` is non-empty (i.e., the wallet already tracks CAT coins under the unrestricted puzzle). More generally, `identify()` should not let an externally-observed coin silently retype/hijack a wallet that already custodies value; conversions of this kind should require prior emptiness of the wallet or explicit user approval.

### Proof of Concept
1. Identify (or choose) a TAIL hash `T` that the victim's wallet already uses for a funded, plain `CATWallet` (e.g., victim minted/received CAT coins with TAIL `T`).
2. As attacker, craft and broadcast a spend bundle producing a CR-CAT eve coin with TAIL `T`, `authorized_providers = [attacker_provider]`, and any `proofs_checker`, using `CRCAT.launch(...)` in `chia/wallet/vc_wallet/cr_cat_drivers.py` (lines 180-271), sending the CR-CAT payment to any puzzle hash observed by the victim's wallet sync.
3. When the victim's wallet processes the resulting coin state, `CATWallet.identify()` (chia/wallet/cat_wallet/cat_wallet.py:515-533) matches on TAIL `T`, finds the victim's existing funded `CATWallet`, and calls `CRCATWallet.convert_to_cr()` unconditionally — no check that the wallet already has non-empty `lineage_store`.
4. The victim's wallet entry for TAIL `T` is now a `CRCATWallet` requiring the attacker's `authorized_providers`/VC to authorize spends, while the victim's actual on-chain coins remain plain (non-CR) CAT coins — the wallet can no longer correctly construct/sign spends for its own pre-existing balance.

### Citations

**File:** chia/wallet/cat_wallet/cat_wallet.py (L515-533)
```python
            if wallet_type in {CRCATWallet, RCATWallet}:
                # We didn't find a matching alt-CAT wallet, but maybe we have a matching CAT wallet that we can convert
                for wallet_info in await wallet_state_manager.get_all_wallet_info_entries(wallet_type=WalletType.CAT):
                    cat_info: CATInfo = CATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    found_cat_wallet = wallet_state_manager.wallets[wallet_info.id]
                    assert isinstance(found_cat_wallet, CATWallet)
                    if cat_info.limitations_program_hash == asset_id:
                        if wallet_type is CRCATWallet:
                            assert crcat is not None  # again, mypy isn't this smart
                            await CRCATWallet.convert_to_cr(
                                found_cat_wallet,
                                crcat.authorized_providers,
                                ProofsChecker.from_program(UnknownPuzzle(known_program=crcat.proofs_checker)),
                            )
                            async with sync_scope.use() as interface:
                                interface.side_effects.websocket_events.append(
                                    WebSocketEvent(name="converted cat wallet to cr", wallet_id=wallet_info.id)
                                )
                            return WalletIdentifier(wallet_info.id, WalletType(WalletType.CRCAT))
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L175-201)
```python
    @classmethod
    async def convert_to_cr(
        cls,
        cat_wallet: CATWallet,
        authorized_providers: list[bytes32],
        proofs_checker: ProofsChecker,
    ) -> None:
        replace_self = cls()
        replace_self.standard_wallet = cat_wallet.standard_wallet
        replace_self.log = logging.getLogger(cat_wallet.get_name())
        replace_self.log.info(f"Converting CAT wallet {cat_wallet.id()} to CR-CAT wallet")
        replace_self.wallet_state_manager = cat_wallet.wallet_state_manager
        replace_self.info = CRCATInfo(
            cat_wallet.cat_info.limitations_program_hash, None, authorized_providers, proofs_checker
        )
        await cat_wallet.wallet_state_manager.user_store.update_wallet(
            WalletInfo(
                cat_wallet.id(), cat_wallet.get_name(), uint8(WalletType.CRCAT.value), bytes(replace_self.info).hex()
            )
        )
        updated_wallet_info = await cat_wallet.wallet_state_manager.user_store.get_wallet_by_id(cat_wallet.id())
        assert updated_wallet_info is not None
        replace_self.wallet_info = updated_wallet_info
        replace_self.tail_hash = replace_self.info.limitations_program_hash

        cat_wallet.wallet_state_manager.wallets[cat_wallet.id()] = replace_self

```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L804-819)
```python
    async def match_puzzle_info(self, puzzle_driver: PuzzleInfo) -> bool:
        if (
            AssetType(puzzle_driver.type()) == AssetType.CAT
            and puzzle_driver["tail"] == self.info.limitations_program_hash
        ):
            inner_puzzle_driver: PuzzleInfo | None = puzzle_driver.also()
            if inner_puzzle_driver is None:
                raise ValueError("Malformed puzzle driver passed to CRCATWallet.match_puzzle_info")  # pragma: no cover
            return (
                AssetType(inner_puzzle_driver.type()) == AssetType.CR
                and [bytes32(provider) for provider in inner_puzzle_driver["authorized_providers"]]
                == self.info.authorized_providers
                and ProofsChecker.from_program(UnknownPuzzle(known_program=inner_puzzle_driver["proofs_checker"]))
                == self.info.proofs_checker
            )
        return False
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L179-187)
```python
    @classmethod
    async def convert_to_revocable(
        cls,
        cat_wallet: CATWallet,
        hidden_puzzle_hash: bytes32,
    ) -> bool:
        if not await cat_wallet.lineage_store.is_empty():
            cat_wallet.log.error("Received a revocable CAT to a CAT wallet that already has CATs")
            return False
```
