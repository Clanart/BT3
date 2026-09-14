## Analysis

The reported bug class is a **front‑running/griefing DoS on a privileged deauthorization action**: an unprivileged party can preempt an admin's state‑removal transaction simply by creating a new pending state for the same account, forcing the admin's transaction to fail against fresh preconditions, indefinitely.

The closest reachable analog in this codebase is the **Verified Credential (VC) revocation flow** between a DID "provider" and a VC "holder."

### Title
VC holder can perpetually front-run provider's on-chain revocation by self-cycling the VC coin - (File: chia/wallet/vc_wallet/vc_wallet.py, chia/wallet/vc_wallet/vc_drivers.py)

### Summary
A VC's puzzle wraps its inner (holder-controlled) puzzle inside a `RevocationLayer` that exposes two spend paths: the normal path controlled by the holder's inner puzzle hash, and a hidden "backdoor" path that only the proof-provider (DID) can activate to revoke the credential. [1](#0-0) 

The provider's revocation (`revoke_vc`) works by fetching the *current* unspent VC coin state from the network, deriving the VC's live parameters from that specific coin, and building a spend against it. [2](#0-1) 

### Finding Description
Because the VC is a singleton coin, only one spend of its current instance can be confirmed. `revoke_vc` requires knowledge of the exact current coin (`parent_id` → fetched `CoinState` → `CoinSpend` → derived `VerifiedCredential`) before it constructs the backdoor spend via `activate_backdoor`. [3](#0-2) 

The holder, however, retains full unilateral control of the "visible" ACS path of the revocation layer and can spend/cycle their VC coin at will (e.g. via `generate_signed_transaction` with no proof change, or a proof update), which is exactly the mechanism `VCWallet.generate_signed_transaction` supports. [4](#0-3) 

If the holder observes a pending `revoke_vc` spend bundle for their VC coin in the mempool (which references a specific parent coin and lineage proof), they can broadcast their own competing spend of the same coin with a higher fee. Once mined, the provider's transaction becomes invalid because it references an already-spent coin, forcing the provider to resync and rebuild the revoke transaction — at which point the holder can repeat the cycle indefinitely. This is structurally identical to the report's front-running of `deauthorizeAccount`: the account/asset holder can perpetually block a privileged deauthorization/revocation transaction by racing new pending state ahead of it.

### Impact Explanation
A malicious VC holder can indefinitely retain a credential the issuing DID intends to revoke, defeating the entire purpose of the revocation backdoor (e.g., continuing to satisfy CR-CAT authorized-provider checks in `add_vc_authorization`/CRCAT flows after the issuer believes the credential has been revoked). This is a Medium-severity griefing/DoS of a security control (credential revocation), not a direct fund-theft primitive, but it undermines the trust/compliance guarantee CR-CAT/VC systems are built to provide. [5](#0-4) 

### Likelihood Explanation
Exploitation requires only a mempool-observing wallet client controlled by the credential holder — no special network position, no malicious node/peer, and no protocol violation. The holder simply needs to watch for spends of their own VC coin ID appearing in the mempool and race a self-spend with a competitive fee, which is achievable by any wallet user.

### Recommendation
Consider redesigning the revocation mechanism so the provider's backdoor authority does not depend on racing a specific coin generation of the holder's VC. Options include: (1) allowing the provider's revocation solution to be valid against any coin along the VC's lineage rather than only the latest tip (e.g., via an announcement/assertion mechanism that survives holder-initiated re-spends), or (2) giving the provider's revocation transaction unconditional priority by having the holder's normal-path spend itself assert non-existence of a pending provider revocation announcement, so a holder cannot "outrun" a provider who has already announced intent to revoke.

### Proof of Concept
1. Provider (DID) detects reason to revoke a VC and submits a `vc_revoke` RPC call, which builds a spend bundle referencing the VC's current coin/lineage via `revoke_vc`. [6](#0-5) 
2. Holder runs a bot watching mempool for spends of their VC coin ID.
3. Upon seeing the pending revoke bundle, holder immediately submits their own spend of the same VC coin (e.g., a no-op proof cycle) with a higher fee via `generate_signed_transaction`. [4](#0-3) 
4. Holder's transaction confirms first; provider's `revoke_vc` bundle is rejected as double-spend since its input coin no longer exists.
5. Provider must resync (`get_coin_state`) and rebuild the revoke transaction against the new coin, and the holder repeats step 3 indefinitely, blocking revocation.

### Citations

**File:** chia/wallet/vc_wallet/vc_drivers.py (L475-482)
```python
    def wrap_inner_with_backdoor(self) -> Program:
        return create_revocation_layer(
            self.hidden_puzzle().get_tree_hash(),
            self.inner_puzzle_hash,
        )

    def hidden_puzzle(self) -> Program:
        return STANDARD_BRICK_PUZZLE
```

**File:** chia/wallet/vc_wallet/vc_drivers.py (L757-789)
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
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L250-266)
```python
    async def generate_signed_transaction(
        self,
        amounts: list[uint64],
        puzzle_hashes: list[bytes32],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        coins: set[Coin] | None = None,
        memos: list[list[bytes]] | None = None,
        extra_conditions: tuple[Condition, ...] = tuple(),
        **kwargs: Unpack[GSTOptionalArgs],
    ) -> None:
        new_proof_hash: bytes32 | None = kwargs.get(
            "new_proof_hash", None
        )  # Requires that this key possesses the DID to update the specified VC
        provider_inner_puzhash: bytes32 | None = kwargs.get("provider_inner_puzhash", None)
        self_revoke: bool | None = kwargs.get("self_revoke", False)
        potential_vc_id: bytes32 | None = kwargs.get("vc_id", None)
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L376-391)
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
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L439-484)
```python
    async def add_vc_authorization(
        self, offer: Offer, solver: Solver, action_scope: WalletActionScope
    ) -> tuple[Offer, Solver]:
        """
        This method takes an existing offer and adds a VC authorization spend to it where it can/is willing.
        The only coins types that it looks for to approve are CR-CATs at the moment.
        It will approve a CR-CAT spend if it meets one of the following conditions:
          - It is coming to this wallet (we know we have a valid VC)
          - It is going back to the same puzzle hash it came from (it is change)
          - It is going to the "pending approval" state (we can defer authorization to another user's VC)
          - It is going to the OFFER_MOD (known puzzlehash, intermediate custody-less state, no VC needed)

        Note that the second requirement above means that to make a valid offer of CR-CATs for something else, you must
        send the change back to it's original puzzle hash or else a taker wallet will not approve it.
        """
        # Gather all of the CRCATs being spent and the CRCATs that each creates
        crcat_spends: list[CRCATSpend] = []
        other_spends: list[CoinSpend] = []
        spends_to_fix: dict[bytes32, CoinSpend] = {}
        for spend in offer.to_valid_spend().coin_spends:
            if CRCAT.is_cr_cat(UnknownPuzzle(known_program=spend.puzzle_reveal))[0]:
                crcat_spend: CRCATSpend = CRCATSpend.from_coin_spend(spend)
                if crcat_spend.incomplete:
                    crcat_spends.append(crcat_spend)
                    if spend in offer._bundle.coin_spends:
                        spends_to_fix[spend.coin.name()] = spend
                elif spend in offer._bundle.coin_spends:  # pragma: no cover
                    other_spends.append(spend)
            elif spend in offer._bundle.coin_spends:
                other_spends.append(spend)

        # Figure out what VC announcements are needed
        announcements_to_make: dict[bytes32, list[CreatePuzzleAnnouncement]] = {}
        announcements_to_assert: dict[bytes32, list[AssertCoinAnnouncement]] = {}
        vcs: dict[bytes32, VerifiedCredential] = {}
        coin_args: dict[str, tuple[Program, bytes32, bytes32, bytes32]] = {}
        for crcat_spend in crcat_spends:
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

**File:** chia/wallet/wallet_rpc_api.py (L3434-3454)
```python
    async def vc_revoke(
        self,
        request: VCRevoke,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> VCRevokeResponse:
        """
        Revoke an on chain VC provided the correct DID is available
        :param request: required 'vc_parent_id' for the VC coin. Standard transaction params 'fee' & 'reuse_puzhash'.
        :return: a list of all relevant 'transactions' (TransactionRecord) that this spend generates (VC TX + fee TX)
        """

        vc_wallet: VCWallet = await self.service.wallet_state_manager.get_or_create_vc_wallet()

        await vc_wallet.revoke_vc(
            request.vc_parent_id,
            self.service.get_full_node_peer(),
            action_scope,
            request.fee,
            extra_conditions=extra_conditions,
        )
```
