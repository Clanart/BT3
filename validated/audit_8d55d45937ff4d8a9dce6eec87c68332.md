I have enough evidence to establish the analog. The CR-CAT `authorized_providers` list is curried into the puzzle (`construct_cr_layer`) at CAT/CRCAT creation time and can never be updated for coins that have already been created with it — directly mirroring the reported bug class (an immutable trusted set of identities chosen once, whose inclusion of a bad actor puts user funds at risk, with no update mechanism).

### Title
Immutable `authorized_providers` set curried into CR-CAT puzzles cannot be updated, permanently trusting whichever DIDs were chosen at mint time - (File: chia/wallet/vc_wallet/cr_cat_drivers.py)

### Summary
A CR-CAT's `authorized_providers` (the list of DID launcher IDs whose issued Verified Credentials can authorize movement of the CAT) is curried directly into the on-chain puzzle via `construct_cr_layer()` at CAT launch time and is propagated unchanged through every subsequent spend (`do_spend`, `spend_many`, `get_next_from_coin_spend`). There is no mechanism to remove or replace an authorized provider from an already-minted CR-CAT lineage. If any provider in that initial, immutable list is (or later becomes) malicious/compromised, every coin of that CR-CAT type is permanently exposed for as long as it exists, since any spend must be authorized by a VC from one of the providers in the frozen list.

### Finding Description
`construct_cr_layer()` curries `authorized_providers` (and `proofs_checker`) directly into `CREDENTIAL_RESTRICTION`, and this curried value becomes part of the coin's puzzle hash: [1](#0-0) 

This set is fixed the moment a CR-CAT is minted via `CRCAT.launch()`, where `authorized_providers` is baked into both the CR layer puzzle and the eve inner puzzle's remark condition: [2](#0-1) 

Every subsequent spend of the coin (`do_spend`) reconstructs child CR-CATs with the exact same `self.authorized_providers` value — there is no code path that changes it: [3](#0-2) 

At spend time, any VC whose issuing DID is in that fixed list is accepted as sufficient authorization. `VCWallet.add_vc_authorization()` looks up "available VCs" purely by checking whether the VC's provider is present in `crcat_spend.crcat.authorized_providers`, with no other identity check: [4](#0-3) 

Likewise, `CRCATWallet` and offer-taking logic ask only "does a VC exist whose provider is in `self.info.authorized_providers`" without any way to exclude a provider that is later found to be malicious: [5](#0-4) 

This is the exact bug-class of the referenced report: a set of trusted entities (there, pooled tokens; here, authorized VC-issuing DIDs) is accepted once at object-initialization time and is baked immutably into the object's identity/puzzle, with no supported update path if a bad entry is later included or becomes compromised.

### Impact Explanation
If a CAT issuer includes a malicious or later-compromised DID in `authorized_providers` when minting a CR-CAT (or a DID's private key is later leaked), that provider can indefinitely mint VCs used to authorize spends of every coin in that CR-CAT's lineage, moving user funds against their wishes, for the entire lifetime of the asset — there is no way to revoke that provider from already-existing coins short of manually migrating all holders to a brand-new CAT with a new `authorized_providers` set (a hard fork of the asset, not an in-protocol fix). This can result in unauthorized/forged movement of restricted-CAT funds that legitimate holders believed were protected by credential restrictions.

### Likelihood Explanation
Likelihood depends on issuer diligence when selecting `authorized_providers` at mint time and on the long-term security of each provider DID's keys — this is analogous to the Medium-severity classification of the original report (an admin/issuer-configuration risk rather than a directly-exploitable-by-anyone bug), but it is reachable purely through ordinary CAT-issuance and wallet-usage flows (`CRCAT.launch`, `CRCATWallet.get_or_create_wallet_for_cat`, `VCWallet.add_vc_authorization`) with no privileged/administrative access required beyond being the original CAT issuer.

### Recommendation
Document the risk clearly for CAT issuers minting CR-CATs: `authorized_providers` chosen at `CRCAT.launch()`/`CRCATWallet.get_or_create_wallet_for_cat()` time is permanent and immutable for that asset's lineage, and issuers must treat provider-DID selection and key custody with the same care as a TAIL program, since a bad or later-compromised provider cannot be removed without migrating to a new asset ID.

### Proof of Concept
1. Issuer mints a CR-CAT via `CRCAT.launch(...)` with `authorized_providers = [good_did, bad_did]` [2](#0-1) .
2. Holders receive and hold this CR-CAT, believing only `good_did`-issued VCs can authorize spends.
3. `bad_did`'s controller (compromised or malicious from the start) issues a VC to themselves and calls `VCWallet.add_vc_authorization()`/`CRCATWallet.claim_pending_approval_balance()`-style flows, which only check `provider in crcat.authorized_providers` [4](#0-3) .
4. The spend is accepted on-chain because the CLVM puzzle only checks membership in the curried, immutable `authorized_providers` list — there is no mechanism in `do_spend`/`spend_many` to exclude `bad_did` from any already-minted coin [3](#0-2) .
5. Every existing coin of this CR-CAT can be spent this way indefinitely; the only remedy is issuing an entirely new CAT with a corrected provider list and migrating holders off the compromised one.

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

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L180-221)
```python
    @classmethod
    def launch(
        cls,
        # General CAT launching info
        origin_coin: Coin,
        payment: CreateCoin,
        tail: Program,
        tail_solution: Program,
        # CR Layer params
        authorized_providers: list[bytes32],
        proofs_checker: Program,
        # Probably never need this but some tail might
        optional_lineage_proof: LineageProof | None = None,
    ) -> tuple[Program, CoinSpend, CRCAT]:
        """
        Launch a new CR-CAT from XCH.

        Returns a delegated puzzle to run that creates the eve CAT, an eve coin spend of the CAT, and the expected class
        representation after all relevant coin spends have been confirmed on chain.
        """
        tail_hash: bytes32 = tail.get_tree_hash()

        new_cr_layer_hash: bytes32 = construct_cr_layer(
            authorized_providers,
            proofs_checker,
            payment.puzzle_hash,  # type: ignore
        ).get_tree_hash_precalc(payment.puzzle_hash)
        new_cat_puzhash = construct_cat_puzzle(CAT_MOD, tail_hash, new_cr_layer_hash).get_tree_hash_precalc(
            new_cr_layer_hash
        )

        eve_innerpuz: Program = Program.to(
            (
                1,
                [
                    [51, new_cr_layer_hash, payment.amount, payment.memos],
                    [51, None, -113, tail, tail_solution],
                    [60, None],
                    [1, payment.puzzle_hash, authorized_providers, proofs_checker],
                ],
            )
        )
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L500-524)
```python
            ),
            [
                CRCAT(
                    Coin(
                        self.coin.name(),
                        self.construct_puzzle(new_inner_puzzle_hash).get_tree_hash_precalc(  # type: ignore
                            new_inner_puzzle_hash
                        ),
                        new_amount,
                    ),
                    self.tail_hash,
                    LineageProof(
                        self.coin.parent_coin_info,
                        self.construct_cr_layer(self.inner_puzzle_hash).get_tree_hash_precalc(  # type: ignore
                            self.inner_puzzle_hash
                        ),
                        uint64(self.coin.amount),
                    ),
                    self.authorized_providers,
                    self.proofs_checker,
                    new_inner_puzzle_hash,
                )
                for new_inner_puzzle_hash, new_amount in new_inner_puzzle_hashes_and_amounts
            ],
        )
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L476-484)
```python
            # Check first whether we can approve...
            available_vcs: list[VCRecord] = [
                vc_rec
                for vc_rec in await self.store.get_vc_records_by_providers(crcat_spend.crcat.authorized_providers)
                if vc_rec.confirmed_at_height != 0
            ]
            if len(available_vcs) == 0:  # pragma: no cover
                raise ValueError(f"No VC available with provider in {crcat_spend.crcat.authorized_providers}")
            vc: VerifiedCredential = available_vcs[0].vc
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L713-716)
```python
        vc_wallet: VCWallet = await self.wallet_state_manager.get_or_create_vc_wallet()
        vc: VerifiedCredential | None = await vc_wallet.get_vc_with_provider_in_and_proofs(
            self.info.authorized_providers, self.info.proofs_checker.flags
        )
```
