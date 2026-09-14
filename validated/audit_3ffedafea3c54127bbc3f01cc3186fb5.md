### Title
CR-CAT funds sent to the "pending approval" state can be permanently stranded if the recipient's VC is revoked before claiming - ([File: chia/wallet/vc_wallet/cr_cat_wallet.py])

### Summary
CR-CAT (credential-restricted CAT) transfers to a puzzle hash that the sending wallet does not recognize as already holding a valid VC are wrapped into an intermediate, VC-gated "pending approval" puzzle. Only a `claim_pending_approval_balance` spend—authorized by a live, non-revoked Verified Credential (VC) matching the CR-CAT's `authorized_providers`—can move the coin into a normal spendable state. If the DID/provider revokes the recipient's VC (via the VC's backdoor) any time before the recipient claims the pending coin, there is no other path to ever spend it: the funds are irrecoverably stuck. This mirrors the reported bridge bug class: value is already "sent"/committed on one side of a two-step protocol, but an out-of-band control (pause in the bridge case, VC revocation here) can permanently block the second step with no fallback or recovery.

### Finding Description
When a `CRCATWallet` sends a payment to a puzzle hash that isn't already known to hold a matching VC (e.g., in an offer settlement, or any external send), the payment is redirected into a "pending approval" puzzle instead of the raw destination puzzle hash: [1](#0-0) 

That coin can only later be moved into spendable balance by `claim_pending_approval_balance`, which mandatorily looks up a still-valid VC: [2](#0-1) 

The "pending approval" puzzle hash itself is a pure function of the destination inner puzzle hash and amount, with no timeout, alternate custody, or provider-independent unlock branch: [3](#0-2) 

The VC that authorizes the claim can be permanently revoked at any point by its `proof_provider` (a DID) using the VC's built-in backdoor: [4](#0-3) [5](#0-4) 

The driver code itself acknowledges the resulting dead-end: once revoked, the VC can never again be recognized/spent as a valid credential, and there is explicitly no alternate recovery state: [6](#0-5) 

If a recipient's only qualifying VC is revoked between the time a CR-CAT payment lands in the "pending approval" puzzle and the time they call `claim_pending_approval_balance`, `get_vc_with_provider_in_and_proofs` will find no eligible VC and the claim can never be constructed—yet the CR-CAT coin's puzzle hash permanently requires exactly that VC-authorized spend path. The originating sender already irreversibly transferred/burned the value into the pending-approval puzzle (analogous to the bridge's source-chain burn); there is no compensating action on the recipient side once the gating credential is gone (analogous to the bridge's paused `handle()` blocking mint with no retry guarantee).

### Impact Explanation
This is a High-impact, reachable-by-ordinary-user finding: any unprivileged wallet user or offer counterparty receiving CR-CATs can have their incoming funds permanently locked if the CR-CAT's authorized DID provider revokes their VC (for any reason—compliance action, provider error, or malice) before the recipient submits the claim spend. There is no code path, timelock, or governance mechanism to unlock or re-route the pending-approval coin once the qualifying VC is gone; the funds are burned from the sender's side (final, confirmed on-chain) with the receiver permanently unable to redeem them.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires (1) a CR-CAT transfer landing in the pending-approval state (a normal, expected condition for many offers/external sends) and (2) a VC revocation event occurring in the window between coin creation and claim. Revocation is a legitimate provider action (not attacker-controlled by the coin's holder), so it is not attacker-triggerable at will, but it is a realistic, foreseeable operational event (compliance revocation, provider key rotation, provider error) given the credential-restricted design's whole purpose is to allow providers to revoke access.

### Recommendation
Add a recovery/fallback path for CR-CAT coins parked in the "pending approval" state, such as: (a) a timelock-gated fallback allowing the coin to revert to the sender or move to an unrestricted state after a bounded period if no valid VC claim occurs, or (b) allowing claim authorization from any VC issued by an authorized provider still listed at claim time even if the specific original VC was revoked, provided proofs are otherwise satisfied. At minimum, document and warn wallet/offer UIs that CR-CAT payments to addresses without a currently valid VC can become permanently unclaimable if the qualifying VC is revoked before claiming.

### Proof of Concept
1. Provider (DID) issues a VC to Bob with `authorized_providers` including the provider.
2. Alice sends Bob a CR-CAT payment via `CRCATWallet.generate_signed_transaction`; since Bob's puzzle hash isn't recognized, the payment is wrapped via `construct_pending_approval_state` (chia/wallet/vc_wallet/cr_cat_wallet.py:622-638) and confirmed on-chain — Alice's coins are spent/committed.
3. Before Bob calls `crcat_approve_pending` / `claim_pending_approval_balance`, the provider calls `revoke_vc` → `VerifiedCredential.activate_backdoor` (chia/wallet/vc_wallet/vc_wallet.py:376-410; chia/wallet/vc_wallet/vc_drivers.py:757-798), permanently revoking Bob's VC.
4. Bob calls `claim_pending_approval_balance`; `get_vc_with_provider_in_and_proofs` finds no valid VC (chia/wallet/vc_wallet/cr_cat_wallet.py:712-718) and raises `RuntimeError`, and no other spend path exists for the pending-approval CR-CAT puzzle hash (chia/wallet/vc_wallet/cr_cat_drivers.py:166-168).
5. Bob's CR-CAT coin remains forever unspendable; Alice's original payment is permanently lost with no recovery mechanism.

### Citations

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

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L166-168)
```python
# For the "pending approval" state
def construct_pending_approval_state(puzzle_hash: bytes32, amount: uint64) -> Program:
    return PENDING_VC_ANNOUNCEMENT.curry(Program.to([[51, puzzle_hash, amount, [puzzle_hash]]]))
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

**File:** chia/wallet/vc_wallet/vc_drivers.py (L757-798)
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
        )
```
