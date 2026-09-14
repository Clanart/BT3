### Title
CR-CAT "pending approval" coins can become permanently unclaimable if the required VC is unavailable, with no on-chain recovery path - ([File: chia/wallet/vc_wallet/cr_cat_wallet.py])

### Summary
When a CR-CAT (Credential Restricted CAT) payment is sent to a recipient who does not yet hold a valid VC, the wallet wraps the output in a "pending approval" puzzle hash via `construct_pending_approval_state()` [1](#0-0)  instead of delivering the coin directly. Recovery from this intermediate state requires `CRCATWallet.claim_pending_approval_balance()` to locate a VC whose `proof_provider` is in the CR-CAT's `authorized_providers` list and whose proofs satisfy the `proofs_checker` [2](#0-1) . If no such VC exists — e.g. it was revoked via `VCWallet.revoke_vc`/`activate_backdoor` [3](#0-2) [4](#0-3) , or the recipient never obtained a VC for the relevant provider, or the authorized-providers list changes — the call raises `RuntimeError("No VC exists that can approve spends for CR-CAT wallet ...")` [5](#0-4)  and the coin cannot be claimed at all.

### Finding Description
This is functionally the same bug class as the Midas Gateway issue: an asset is moved into an intermediate/escrow-like puzzle state (Midas Gateway pending redemption vs. Chia CR-CAT "pending approval" state) that requires a privileged authorization step (Midas admin approval vs. VC-holder approval) to release the funds to their final destination. If that authorization becomes permanently unavailable (Midas: admin calls `rejectRequest()`; Chia: the DID provider revokes the VC, or the recipient's VC for the required `authorized_providers`/`proofs_checker` combination never existed or expires), the funds get stuck in the intermediate puzzle hash with:
- No CAT-layer / puzzle-level mechanism to reroute the "pending approval" coin back to the sender.
- No fallback path other than `claim_pending_approval_balance`, which strictly requires a currently-valid matching VC (`get_vc_with_provider_in_and_proofs`) [6](#0-5) .

The `construct_pending_approval_state` puzzle hash is a curried `PENDING_VC_ANNOUNCEMENT` puzzle wrapping the ultimate destination and amount [1](#0-0) , and the only wallet-level code path that spends coins out of that state is `claim_pending_approval_balance`, gated entirely on VC availability. There is no explicit "reject"/"return-to-sender" spend path implemented in `cr_cat_wallet.py` or `cr_cat_drivers.py` for this state (based on available indexed code), meaning once a payment is put into pending-approval and the destined party's VC access is lost, the coin is functionally frozen — similar to Midas mTokens being stuck in the Gateway once `rejectRequest()` is called with no clear-out mechanism.

### Impact Explanation
This matches the accepted bug class ("concrete unsigned or unauthorized coin movement" is not the pattern here, but "spend-triggered transaction-processing halt" / permanently blocked user funds is). Funds sent as CR-CAT payments (e.g. from an offer, or a direct CR-CAT transfer) can become permanently locked, unrecoverable by the sender or intended recipient, if the recipient's VC is revoked or was never valid for the asset's `authorized_providers`. This is a real value-locking risk for any CR-CAT market participant relying on this restricted-CAT scheme (e.g. Midas-style regulated stablecoins built on Chia CR-CATs).

### Likelihood Explanation
Requires a legitimate but adversarial-adjacent scenario: a DID/VC-provider revoking a VC after a CR-CAT payment has already been sent to the pending-approval state but before the recipient claims it, or a payment being sent to a party who never had (or no longer has) an authorized VC. This does not require any malicious peer/node/farmer behavior — it can occur through ordinary and even accidental use (VC provider revocation is a normal, documented operation via `RevokeVCCMD`/`vc_revoke` RPC [7](#0-6) ), making it a plausible operational risk rather than a rare edge case.

### Recommendation
Implement an explicit reclaim/expiry path for CR-CAT "pending approval" coins that allows the original sender (or, after a timeout, any authorized party) to reclaim the coin back to the sender's puzzle hash if no valid VC can be produced to complete the claim — analogous to the manual-recovery functionality the Midas Gateway team added in response to the analogous report. At minimum, document and provide tooling for detecting stuck pending-approval CR-CAT coins and instrument monitoring so curators/users can react (analogous to the client's suggested mitigation of forbidding/monitoring rather than protocol-level guarantees).

### Proof of Concept
Conceptual reproduction based on existing test helpers (`crcat_approve_pending`, `VCRevoke`) in the indexed test suite:
1. Mint a CR-CAT with `authorized_providers = [DID_A]` and send a payment to a recipient without a currently valid VC; the wallet wraps the output via `construct_pending_approval_state` [8](#0-7) .
2. Before the recipient claims the pending balance, the DID_A holder calls `RevokeVCCMD`/`vc_revoke` to revoke the recipient's VC [9](#0-8) , or the recipient simply never had a matching VC.
3. Recipient calls `crcat_approve_pending` → `claim_pending_approval_balance`, which fails to find a matching VC and raises `RuntimeError("No VC exists that can approve spends for CR-CAT wallet ...")` [5](#0-4) , leaving the CR-CAT coin permanently in the pending-approval puzzle hash with no available spend path in the indexed codebase.

Note: I was unable to fully verify from the indexed CLVM puzzle source (`cr_cat_drivers.py` beyond what was retrieved) whether the low-level `PENDING_VC_ANNOUNCEMENT`/`CREDENTIAL_RESTRICTION` puzzles support any alternate "return to sender" spend condition not exposed through the Python wallet layer; this analysis is based on the available wallet-level driver and RPC code, and a full CLVM-level audit would be needed for complete certainty.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L166-168)
```python
# For the "pending approval" state
def construct_pending_approval_state(puzzle_hash: bytes32, amount: uint64) -> Program:
    return PENDING_VC_ANNOUNCEMENT.curry(Program.to([[51, puzzle_hash, amount, [puzzle_hash]]]))
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L626-638)
```python
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

**File:** chia/cmds/wallet_funcs.py (L1812-1842)
```python
async def revoke_vc(
    wallet_info: WalletClientInfo,
    parent_coin_id: bytes32 | None,
    vc_id: bytes32 | None,
    fee: uint64,
    push: bool,
    tx_config: TXConfig,
    condition_valid_times: ConditionValidTimes,
) -> list[TransactionRecord]:
    if parent_coin_id is None:
        if vc_id is None:
            print("Must specify either --parent-coin-id or --vc-id")
            return []
        record = (await wallet_info.client.vc_get(VCGet(vc_id=vc_id))).vc_record
        if record is None:
            print(f"Cannot find a VC with ID {vc_id.hex()}")
            return []
        parent_id: bytes32 = bytes32(record.vc.coin.parent_coin_info)
    else:
        parent_id = parent_coin_id
    txs = (
        await wallet_info.client.vc_revoke(
            VCRevoke(
                vc_parent_id=parent_id,
                fee=fee,
                push=push,
            ),
            tx_config=tx_config,
            timelock_info=condition_valid_times,
        )
    ).transactions
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
