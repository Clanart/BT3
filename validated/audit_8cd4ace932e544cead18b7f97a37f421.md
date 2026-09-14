### Title
CR-CAT payments locked in "pending approval" state are permanently unrecoverable if the recipient never obtains an authorized VC - ([File: chia/wallet/vc_wallet/cr_cat_wallet.py])

### Summary
The Sherlock report describes an ERC20-blocklist scenario where a push-payment to a designated recipient permanently fails and locks funds because the receiving address becomes unable to receive transfers. Chia's credential-restricted CAT (CR-CAT) mechanism has an analogous "push-to-pending-state, pull-to-claim" design: when a CR-CAT payment is sent to a puzzle hash that the wallet doesn't recognize, it is wrapped in a `pending approval` state that can only be released by the *recipient* presenting a valid Verified Credential (VC) matching the CAT's `authorized_providers`/`proofs_checker`. If the intended recipient never has (or loses) such a valid VC, the locked coin cannot be claimed by anyone, and there is no fallback path for the original sender to reclaim it.

### Finding Description
When a `CRCATWallet` sends a payment to a puzzle hash it does not control, `generate_signed_transaction` wraps the destination puzzle hash in a "pending approval" construction rather than sending directly: [1](#0-0) 

This is analogous to the vulnerable `safeTransferFrom` push pattern in the Teller report — the payer's spend forces value into a state that only the *recipient* can subsequently withdraw, rather than crediting them directly.

To release funds from this pending state, `claim_pending_approval_balance` requires the wallet to hold a VC that satisfies the CR-CAT's `authorized_providers` and `proofs_checker`. If no such VC exists, the claim raises `RuntimeError` and cannot proceed: [2](#0-1) 

The authorized-providers list and proof requirements are fixed per CR-CAT asset at wallet-creation time (`CRCATInfo`), and a VC can be revoked at any time by its credential provider's DID via the backdoor/revocation layer: [3](#0-2) [4](#0-3) 

The pending-approval coin's spend path (`CRCAT.spend_many`/`do_spend`) is only satisfiable by supplying valid proof-of-inclusion for an unrevoked VC issued by one of the `authorized_providers` — there is no code path allowing the original sender to reclaim a coin once it has moved into the pending-approval puzzle hash if the intended recipient can never produce a satisfying VC: [5](#0-4) 

Root cause mirrors the report: value transfer is architected as "push funds to a state gated by the counterparty's continued eligibility," with no allowance for the counterparty's eligibility (VC validity/authorized-provider membership) becoming permanently unsatisfiable after the funds are already committed.

### Impact Explanation
Any offer, CAT trade, or direct payment involving a CR-CAT asset that targets a wallet without an already-existing, in-scope VC (or whose VC is subsequently revoked by the provider, or whose proof set no longer satisfies `proofs_checker`) results in the transferred coins becoming permanently unspendable/unclaimable by any party. This is a coin-set divergence/fund-lock condition reachable purely by a normal wallet user or offer counterparty completing a CR-CAT trade — no privileged or malicious-node action is required. Funds are not stolen, but they are irrecoverably frozen, which is a High-severity availability/loss-of-funds issue for the affected asset holders.

### Likelihood Explanation
Likelihood is significant in any deployment that actually uses CR-CATs (regulated/permissioned CAT assets), because: (1) VCs are revocable by design via the provider DID backdoor at any time after a trade is initiated but before the recipient claims, and (2) a recipient may legitimately never possess a VC satisfying the specific `authorized_providers`/`proofs_checker` combination of a given CR-CAT (e.g., receiving an unsolicited/airdropped CR-CAT, or a CAT-for-CAT offer settlement to a wallet lacking the required credential). Both are ordinary, non-adversarial usage scenarios of the documented CR-CAT/VC flow.

### Recommendation
Introduce a bounded reclaim/refund path for CR-CAT coins sitting in the pending-approval (`CoinType.CRCAT_PENDING`) state — e.g., allow the original sender (or a timelocked fallback authority) to reclaim the coin back to their own inner puzzle hash if it is not claimed by the recipient within some window, similar to the clawback mechanism (`chia/wallet/clawback_manager.py`) already used for standard XCH sends. At minimum, wallet/CLI tooling and the offer-settlement flow (`chia/wallet/trade_manager.py check_for_requested_payment_modifications`) should refuse to route a CR-CAT payment into the pending-approval state without first verifying that the recipient already possesses a valid, unrevoked VC for the asset's `authorized_providers`, to avoid creating deadlocked funds in the first place.

### Proof of Concept
1. Wallet A mints or holds a CR-CAT with `authorized_providers = [P]` and a given `proofs_checker`.
2. Wallet A creates/accepts an offer sending CR-CAT amount `N` to Wallet B's puzzle hash, which Wallet A's wallet does not recognize, causing `generate_signed_transaction` to wrap the payment in `construct_pending_approval_state(puzhash, amount)` (`chia/wallet/vc_wallet/cr_cat_wallet.py:622-638`).
3. Wallet B has no VC issued by provider `P` (or its existing VC is revoked by `P`'s DID via `VCWallet.revoke_vc`/`VerifiedCredential.activate_backdoor`, `chia/wallet/vc_wallet/vc_drivers.py:757-797`).
4. Wallet B calls `claim_pending_approval_balance`; because `vc_wallet.get_vc_with_provider_in_and_proofs` returns `None`, the call raises `RuntimeError("No VC exists that can approve spends for CR-CAT wallet ...")` (`chia/wallet/vc_wallet/cr_cat_wallet.py:712-718`), and no alternate spend path exists to redirect the coin back to Wallet A.
5. The pending-approval CR-CAT coin remains permanently locked on-chain — neither party can ever spend it.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L430-500)
```python
        regular_chia_to_claim: int = 0
        if payment_amount > starting_amount:
            # TODO: The no coverage comment is because minting is broken for both this and the standard CAT wallet
            fee = uint64(fee + payment_amount - starting_amount)  # pragma: no cover
        elif payment_amount < starting_amount:
            regular_chia_to_claim = payment_amount

        need_chia_transaction = (fee > 0 or regular_chia_to_claim > 0) and (fee - regular_chia_to_claim != 0)

        # Calculate standard puzzle solutions
        change = selected_cat_amount - starting_amount
        primaries: list[CreateCoin] = []
        for payment in payments:
            primaries.append(payment)

        if change > 0:
            origin_crcat_record = await self.wallet_state_manager.coin_store.get_coin_record(
                next(iter(cat_coins)).name()
            )
            if origin_crcat_record is None:
                raise RuntimeError("A CR-CAT coin was selected that we don't have a record for")  # pragma: no cover
            origin_crcat = self.coin_record_to_crcat(origin_crcat_record)

            if add_authorizations_to_cr_cats:
                change_puzhash = await action_scope.get_puzzle_hash(self.wallet_state_manager)
            else:
                change_puzhash = origin_crcat.inner_puzzle_hash
            for payment in payments:
                if change_puzhash == payment.puzzle_hash and change == payment.amount:
                    # We cannot create two coins has same id, create a new puzhash for the change
                    change_puzhash = await action_scope.get_puzzle_hash(
                        self.wallet_state_manager, override_reuse_puzhash_with=False
                    )
                    break
            primaries.append(CreateCoin(change_puzhash, uint64(change), [change_puzhash]))

        # Find the VC Wallet
        vc_wallet: VCWallet
        for wallet in self.wallet_state_manager.wallets.values():
            if WalletType(wallet.type()) == WalletType.VC:
                assert isinstance(wallet, VCWallet)
                vc_wallet = wallet
                break
        else:
            raise RuntimeError("CR-CATs cannot be spent without an appropriate VC")  # pragma: no cover

        # Loop through the coins we've selected and gather the information we need to spend them
        vc: VerifiedCredential | None = None
        vc_announcements_to_make: list[bytes] = []
        inner_spends: list[tuple[CRCAT, int, Program, Program]] = []
        first = True
        announcement: CreateCoinAnnouncement | None = None
        coin_ids: list[bytes32] = [coin.name() for coin in cat_coins]
        coin_records: list[WalletCoinRecord] = (
            await self.wallet_state_manager.coin_store.get_coin_records(coin_id_filter=HashFilter.include(coin_ids))
        ).records
        assert len(coin_records) == len(cat_coins)
        # sort the coin records to ensure they are in the same order as the CAT coins
        coin_records = [rec for rec in sorted(coin_records, key=lambda rec: coin_ids.index(rec.coin.name()))]
        for coin in coin_records:
            if vc is None:
                vc = await vc_wallet.get_vc_with_provider_in_and_proofs(
                    self.info.authorized_providers, self.info.proofs_checker.flags
                )

            if cat_discrepancy is not None:
                cat_condition = UnknownCondition(
                    opcode=Program.to(51),
                    args=[
                        Program.NIL,
                        Program.to(-113),
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L622-638)
```python
        payments = []
        for amount, puzhash, memo_list in zip(amounts, puzzle_hashes, memos):
            memos_with_hint: list[bytes] = [puzhash]
            memos_with_hint.extend(memo_list)
            # Force wrap the outgoing coins in the pending state if not going to us
            payments.append(
                CreateCoin(
                    (
                        construct_pending_approval_state(puzhash, amount).get_tree_hash()
                        if puzhash != Offer.ph()
                        and not await self.wallet_state_manager.puzzle_store.puzzle_hash_exists(puzhash)
                        else puzhash
                    ),
                    amount,
                    memos_with_hint,
                )
            )
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L712-723)
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
        proof_of_inclusions: Program = await vc_wallet.proof_of_inclusions_for_root_and_keys(
            vc.proof_hash, self.info.proofs_checker.flags
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
