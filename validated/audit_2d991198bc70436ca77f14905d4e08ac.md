### Title
CR-CAT `authorized_providers` list is permanently immutable once curried into the coin, freezing funds if all providers become unusable — analog of MultisigManager's un-replaceable fixed registry ([File: chia/wallet/vc_wallet/cr_cat_drivers.py])

### Summary
The GoGoPool bug is that `MultisigManager` hard-caps a registry at 10 entries with no way to replace/remove disabled entries, so if all slots become disabled the system is permanently unable to add a valid signer. Chia's CR-CAT (credential-restricted CAT) puzzle has an analogous fixed, un-replaceable authorization registry: the `authorized_providers` list is curried directly into the CR layer of the CAT puzzle at mint time and is never updatable on an existing coin.

### Finding Description
`construct_cr_layer` curries `authorized_providers` (a `list[bytes32]` of DID launcher IDs) directly into `CREDENTIAL_RESTRICTION`, and that curried value becomes part of the coin's puzzle hash for every descendant coin in the CR-CAT's lineage: [1](#0-0) 

Every time a new CR-CAT coin is produced (`launch`, `get_next_from_coin_spend`), the same `authorized_providers` list is threaded through as an immutable field of the `CRCAT` dataclass and re-curried into the next coin's puzzle: [2](#0-1) [3](#0-2) 

To spend a CR-CAT coin (either directly or via `claim_pending_approval_balance` / offer settlement), the wallet must locate a `VerifiedCredential` (VC) whose `proof_provider` DID is a member of `authorized_providers`: [4](#0-3) 

There is no on-chain mechanism analogous to a "replace old multisig" method for this puzzle: `authorized_providers` cannot be edited, appended to, or migrated for an existing CR-CAT coin lineage — it is baked into the puzzle hash forever. The only "provider management" primitives that exist operate on the VC side (`revoke_vc`, `activate_backdoor`, self-revoke) and change whether a given DID's *VC* is valid, not the CAT's fixed provider list: [5](#0-4) [6](#0-5) 

If every DID in a CR-CAT's `authorized_providers` set becomes unable to issue a valid VC (DID coin melted/exited by its own operator, VC revoked, or VC coin lost/cleared via `magic_condition_for_self_revoke`), no future VC from *any* provider in that fixed list can ever authorize a spend of the CR-CAT coin again, because the list itself cannot be changed. This mirrors the GoGoPool `MULTISIG_LIMIT`/no-replace bug precisely: a fixed, capped authorization set baked into consensus-relevant state with no update path.

### Impact Explanation
If the set of `authorized_providers` for a CR-CAT asset all become unusable, every holder of that CR-CAT (which could represent a real-world security token, KYC-gated asset, etc.) permanently loses the ability to spend those coins beyond the CR layer's fallback offer/ACS paths that are still gated by the same provider check. This is a spend-triggered transaction-processing halt for that asset class: coins remain in the UTXO set but can never be moved again by ordinary owners, which is consistent with the "spend-triggered transaction-processing halt" category in the validation criteria. Because CR-CATs are explicitly designed for regulated/attested assets, an issuer whose provider DID set is fully invalidated (deliberately or accidentally) permanently bricks user funds with no recovery mechanism, unlike a standard singleton/DID which can rotate keys or inner puzzles.

### Likelihood Explanation
This requires a specific, somewhat rare precondition — all DIDs in a CR-CAT's `authorized_providers` list simultaneously becoming unable to produce valid proofs (e.g., all provider DIDs revoked/melted or all VCs for that CR-CAT self-revoked without any provider ever expanding the list before that happens). Issuers are expected to only include a small number of providers, making this more likely than in a system with 10 slots, but it still depends on issuer/provider operational failure rather than an attacker-controlled single spend bundle. It is a design-level "no way out" limitation rather than an easily attacker-triggered exploit, similar to the original Medium-severity GoGoPool finding, which was accepted as valid but deprioritized by the project team ("not fixing right now... will upgrade as necessary").

### Recommendation
Introduce a mechanism to migrate/rotate a CR-CAT's `authorized_providers` set without requiring a brand-new asset (e.g., an authorized "governance" spend path recognized by the CR layer that allows a supermajority of existing/incoming providers, or a designated CAT-level admin, to re-curry a coin into a new CR layer with an updated provider list), or document and provide tooling for a graceful "melt to non-CR asset" / "reissue" exit path that does not depend on any of the original `authorized_providers` remaining functional. At minimum, wallet/CLI tooling should warn users and issuers when a CR-CAT's entire provider set has become non-functional so they can proactively re-mint before losing all providers.

### Proof of Concept
1. Mint a CR-CAT with `authorized_providers = [DID_A, DID_B]` via `CRCAT.launch` / `mint_cr_cat`, curried permanently into the puzzle as shown in `construct_cr_layer`. [1](#0-0) 
2. Both `DID_A` and `DID_B` self-revoke or otherwise invalidate their VCs (`revoke_vc` / `magic_condition_for_self_revoke`), so no VC exists with `proof_provider` in `{DID_A, DID_B}`. [7](#0-6) 
3. Attempt to spend the CR-CAT (e.g., via `claim_pending_approval_balance`), which requires `get_vc_with_provider_in_and_proofs(self.info.authorized_providers, ...)` to find a valid VC among the fixed provider set; this fails permanently because `authorized_providers` cannot be updated for the already-minted coin. [4](#0-3) 
4. The CR-CAT coin is now permanently stuck: there is no code path in `cr_cat_drivers.py`/`cr_cat_wallet.py` to replace or expand `authorized_providers` on an existing coin, so the holder's funds are frozen indefinitely — the same "fixed registry, no replacement" failure mode described in the MultisigManager report.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L94-104)
```python
def construct_cr_layer(
    authorized_providers: list[bytes32],
    proofs_checker: Program,
    inner_puzzle: Program,
) -> Program:
    first_curry: Program = CREDENTIAL_RESTRICTION.curry(
        CREDENTIAL_STRUCT,
        authorized_providers,
        proofs_checker,
    )
    return first_curry.curry(first_curry.get_tree_hash(), inner_puzzle)
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L173-178)
```python
    coin: Coin
    tail_hash: bytes32
    lineage_proof: LineageProof
    authorized_providers: list[bytes32]
    proofs_checker: Program
    inner_puzzle_hash: bytes32
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L280-285)
```python
    def construct_cr_layer(self, inner_puzzle: Program) -> Program:
        return construct_cr_layer(
            self.authorized_providers,
            self.proofs_checker,
            inner_puzzle,
        )
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L712-720)
```python
        # Select the relevant VC coin
        vc_wallet: VCWallet = await self.wallet_state_manager.get_or_create_vc_wallet()
        vc: VerifiedCredential | None = await vc_wallet.get_vc_with_provider_in_and_proofs(
            self.info.authorized_providers, self.info.proofs_checker.flags
        )
        if vc is None:  # pragma: no cover
            raise RuntimeError(f"No VC exists that can approve spends for CR-CAT wallet {self.id()}")
        if vc.proof_hash is None:
            raise RuntimeError(f"VC {vc.launcher_id} has no proofs to authorize transaction")  # pragma: no cover
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L376-410)
```python
    async def revoke_vc(
        self,
        parent_id: bytes32,
        peer: WSChiaConnection,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        vc_coin_states: list[CoinState] = await self.wallet_state_manager.wallet_node.get_coin_state(
            [parent_id], peer=peer
        )
        if vc_coin_states is None:
            raise ValueError(f"Cannot find verified credential coin: {parent_id.hex()}")  # pragma: no cover
        vc_coin_state = vc_coin_states[0]
        cs: CoinSpend = await fetch_coin_spend_for_coin_state(vc_coin_state, peer)
        vc: VerifiedCredential = VerifiedCredential.get_next_from_coin_spend(cs)

        # Check if we own the DID
        did_wallet: DIDWallet
        for _, wallet in self.wallet_state_manager.wallets.items():
            if wallet.type() == WalletType.DECENTRALIZED_ID:
                assert isinstance(wallet, DIDWallet)
                if bytes32.fromhex(wallet.get_my_DID()) == vc.proof_provider:
                    did_wallet = wallet
                    break
        else:
            await self.generate_signed_transaction(
                [uint64(1)],
                [await action_scope.get_puzzle_hash(self.wallet_state_manager)],
                action_scope,
                fee,
                vc_id=vc.launcher_id,
                self_revoke=True,
            )
            return
```

**File:** chia/wallet/vc_wallet/vc_drivers.py (L686-697)
```python
    def magic_condition_for_self_revoke(self) -> Program:
        magic_condition: Program = Program.to(
            [
                -10,
                self.eml_lineage_proof.to_program(),
                [
                    Program.to(self.eml_lineage_proof.parent_proof_hash),
                    self.launcher_id,
                ],
                ACS_TRANSFER_PROGRAM.get_tree_hash(),
            ]
        )
```

**File:** chia/wallet/vc_wallet/vc_drivers.py (L757-797)
```python
    def activate_backdoor(
        self, provider_innerpuzhash: bytes32, announcement_nonce: bytes32 | None = None
    ) -> tuple[CreatePuzzleAnnouncement, CoinSpend]:
        """
        Activates the backdoor in the VC to revoke the credentials and remove the provider's DID.

        Returns the announcement we expect from the provider's DID authorizing this, and the spend of the VC.
        Sync attempts by this class on spends generated by this method are expected to fail. This could be improved in
        the future with a separate type/state of VC that is revoked, but perfectly useful as a singleton.
        """
        vc_solution: Program = solution_for_singleton(
            self.singleton_lineage_proof,
            uint64(self.coin.amount),
            Program.to(
                [  # solve EML
                    solve_revocation_layer(
                        self.hidden_puzzle(),
                        solve_std_vc_backdoor(
                            self.launcher_id,
                            Program.to((self.proof_provider, self.proof_hash)).get_tree_hash(),
                            self.construct_transfer_program().get_tree_hash(),
                            self.inner_puzzle_hash,
                            uint64(self.coin.amount),
                            self.eml_lineage_proof,
                            provider_innerpuzhash,
                            self.coin.name(),
                            announcement_nonce,
                        ),
                        hidden=True,
                    ),
                ]
            ),
        )

        expected_announcement: CreatePuzzleAnnouncement = CreatePuzzleAnnouncement(
            std_hash(self.coin.name() + Program.NIL.get_tree_hash() + ACS_TRANSFER_PROGRAM.get_tree_hash())
        )

        return (
            expected_announcement,
            make_spend(self.coin, self.construct_puzzle(), vc_solution),
```
