### Title
CR-CAT payments become permanently unrecoverable if the recipient can never obtain an authorized-provider VC to approve the "pending approval" state - ([File: chia/wallet/vc_wallet/cr_cat_drivers.py])

### Summary
The `Stream.sol` bug is that funds get permanently stuck in the contract because a required counterparty (`recipient`) can be excluded by an external authority (USDC blacklist) from ever receiving the transfer, and there's no fallback to let the other party reclaim the funds. The credential-restricted CAT (CR-CAT / VC) flow in this codebase has an analogous structural weakness: a CR-CAT payment can only be finalized to the recipient's real inner puzzle hash if the recipient (or someone acting on their behalf) later produces a valid VC issued by one of the `authorized_providers` [1](#0-0) . Until that happens, the CAT sits in an on-chain "pending approval" puzzle hash [2](#0-1) . There is no sender-side reclaim/cancel path once the coin has been spent to that pending state.

### Finding Description
When a CR-CAT payment is made to a recipient, `check_for_requested_payment_modifications` (offers) or the normal CR-CAT `_generate_unsigned_spendbundle` change/payment logic wraps the destination puzzle hash into a `construct_pending_approval_state(payment.puzzle_hash, payment.amount)` puzzle hash instead of sending directly to the recipient's inner puzzle [3](#0-2) . This coin is now irrevocably committed on-chain — the sender's CAT coin has already been spent and consumed.

To move the coin out of the pending state into the actual recipient inner puzzle, `claim_pending_approval_balance` must be invoked, which requires locating a `VerifiedCredential` whose `proof_provider` is in `self.info.authorized_providers` and whose proofs satisfy `self.info.proofs_checker.flags` [4](#0-3) . If no such VC exists, the code explicitly raises `RuntimeError(f"No VC exists that can approve spends for CR-CAT wallet {self.id()}")` [5](#0-4) .

This is directly analogous to a "blacklisted recipient": if the recipient can never obtain a valid VC from any of the `authorized_providers` for that CR-CAT (e.g., their DID-issued VC is revoked via `activate_backdoor`/`revoke_vc` [6](#0-5) , or the provider simply refuses/never issues one to them), the coin permanently remains in the `pending_approval` puzzle hash. Unlike `Stream.cancel()`, there is no code path anywhere in `CRCATWallet` or `VCWallet` that allows the **sender** to reclaim the pending-state coin back to themselves if the intended recipient can never satisfy the credential check. The only exit from the pending-approval puzzle is via `claim_pending_approval_balance`, which strictly requires a valid, authorized-provider VC [7](#0-6) .

### Impact Explanation
If the credential-restriction gate can never be satisfied for a given pending-approval coin (equivalent to USDC blacklisting), the underlying value is permanently locked with no possibility of recovery by the payer or anyone else. This matches the report's "impact" characterization — permanently stuck funds with no rescue mechanism — but is limited to CR-CAT wallets, a specialized/permissioned asset type, not standard XCH/CAT transfers.

### Likelihood Explanation
Likelihood is limited by design intent: CR-CATs are inherently permissioned assets whose entire purpose is to restrict transfers to credentialed parties, and VC issuance/revocation is a decentralized, provider-controlled process by design (analogous to, but expected/intentional unlike, USDC's centralized blacklist). Whether this constitutes a "bug" versus expected behavior of a permissioned asset type is uncertain — I could not find any documented "reclaim to sender" mechanism or evidence that its absence was considered a defect versus an accepted limitation of the CR-CAT design.

### Recommendation
Consider adding a sender-recoverable timeout/reclaim path for coins that remain in the `pending_approval` state indefinitely (e.g., an alternate branch in the pending-approval puzzle allowing the original sender to reclaim after a timelock if no valid VC ever authorizes the transfer), similar to how the clawback puzzle (`P2_1_OF_N`) provides both a sender-clawback branch and a recipient-claim branch [8](#0-7) .

### Proof of Concept
Not independently verified beyond static code review; I was unable to run/construct an on-chain reproduction within this session. The chain of evidence is:
1. Payment to CR-CAT recipient gets routed to `construct_pending_approval_state` [2](#0-1) .
2. Only `claim_pending_approval_balance` can move it out, and it hard-requires a matching VC or raises `RuntimeError` [9](#0-8) .
3. No `reclaim`/`refund` method targeting `CoinType.CRCAT_PENDING` back to the sender exists in `CRCATWallet` based on my searches of the class [10](#0-9) .

**Uncertainty / caveats**: I was not able to fully confirm there is no alternative recovery mechanism elsewhere in the codebase (e.g., a DAO-level or provider-level rescue path), and this finding hinges on interpreting VC non-issuance/revocation as "recipient blacklisted," which may be considered intended behavior for a permissioned asset class rather than a vulnerability. Given the scope constraints (the rules explicitly permit CAT/CR-CAT/VC flow analogs, and explicitly reject "no-impact" analogs), this is presented as the closest legitimate analog found, but confidence is medium given the design-intent ambiguity.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L227-315)
```python
    async def add_crcat_coin(self, coin_spend: CoinSpend, coin: Coin, height: uint32) -> None:
        try:
            new_cr_cats: list[CRCAT] = CRCAT.get_next_from_coin_spend(coin_spend)
            hint_dict = {
                id: hc.hint
                for id, hc in compute_spend_hints_and_additions(coin_spend)[0].items()
                if hc.hint is not None
            }
            cr_cat: CRCAT = next(filter(lambda c: c.coin.name() == coin.name(), new_cr_cats))
            if (
                await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
                    cr_cat.inner_puzzle_hash
                )
                is not None
            ):
                self.log.info(f"Found CRCAT coin {coin.name().hex()}")
                is_pending = False
            elif (
                cr_cat.inner_puzzle_hash
                == construct_pending_approval_state(
                    hint_dict[coin.name()],
                    uint64(coin.amount),
                ).get_tree_hash()
            ):
                self.log.info(f"Found pending approval CRCAT coin {coin.name().hex()}")
                is_pending = True
                created_timestamp = await self.wallet_state_manager.wallet_node.get_timestamp_for_height(uint32(height))
                spend_bundle = WalletSpendBundle([coin_spend], G2Element())
                memos = compute_memos(spend_bundle)
                # This will override the tx created in the wallet state manager
                tx_record = TransactionRecord(
                    confirmed_at_height=height,
                    created_at_time=uint64(created_timestamp),
                    to_puzzle_hash=hint_dict[coin.name()],
                    to_address=self.wallet_state_manager.encode_puzzle_hash(hint_dict[coin.name()]),
                    amount=uint64(coin.amount),
                    fee_amount=uint64(0),
                    confirmed=True,
                    sent=uint32(0),
                    spend_bundle=None,
                    additions=[coin],
                    removals=[coin_spend.coin],
                    wallet_id=self.id(),
                    sent_to=[],
                    trade_id=None,
                    type=uint32(TransactionType.INCOMING_CRCAT_PENDING),
                    name=coin.name(),
                    memos=memos,
                    valid_times=ConditionValidTimes(),
                )
                await self.wallet_state_manager.tx_store.add_transaction_record(tx_record)
            else:  # pragma: no cover
                self.log.error(f"Unknown CRCAT inner puzzle, coin ID: {coin.name().hex()}")
                return None
            coin_record = WalletCoinRecord(
                coin,
                uint32(height),
                uint32(0),
                False,
                False,
                WalletType.CRCAT,
                self.id(),
                CoinType.CRCAT_PENDING if is_pending else CoinType.CRCAT,
                VersionedBlob(
                    CRCATVersion.V1.value,
                    bytes(
                        CRCATMetadata(
                            cr_cat.lineage_proof, hint_dict[coin.name()] if is_pending else cr_cat.inner_puzzle_hash
                        )
                    ),
                ),
            )
            await self.wallet_state_manager.coin_store.add_coin_record(coin_record)
        except Exception:
            # The parent is not a CAT which means we need to scrub all of its children from our DB
            self.log.error(f"Cannot add CRCAT coin: {traceback.format_exc()}")
            child_coin_records = await self.wallet_state_manager.coin_store.get_coin_records_by_parent_id(
                coin_spend.coin.name()
            )
            if len(child_coin_records) > 0:
                for record in child_coin_records:
                    if record.wallet_id == self.id():  # pragma: no cover
                        await self.wallet_state_manager.coin_store.delete_coin_record(record.coin.name())
                        # We also need to make sure there's no record of the transaction
                        await self.wallet_state_manager.tx_store.delete_transaction_record(record.coin.name())

    def require_derivation_paths(self) -> bool:
        return False

```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L675-723)
```python
    async def claim_pending_approval_balance(
        self,
        min_amount_to_claim: uint64,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        coins: set[Coin] | None = None,
        min_coin_amount: uint64 | None = None,
        max_coin_amount: uint64 | None = None,
        excluded_coin_amounts: list[uint64] | None = None,
        reuse_puzhash: bool | None = None,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        # Select the relevant CR-CAT coins
        crcat_records: set[WalletCoinRecord] = await self.wallet_state_manager.coin_store.get_unspent_coins_for_wallet(
            self.id(), CoinType.CRCAT_PENDING
        )
        if coins is None:
            if max_coin_amount is None:
                max_coin_amount = uint64(self.wallet_state_manager.constants.MAX_COIN_AMOUNT)
            coins = await select_coins(
                await self.get_pending_approval_balance(),
                action_scope.config.tx_config.coin_selection_config,
                list(crcat_records),
                {},
                self.log,
                uint128(min_amount_to_claim),
            )

        # Select the relevant XCH coins
        if fee > 0:
            chia_coins = await self.standard_wallet.select_coins(
                fee,
                action_scope,
            )
        else:
            chia_coins = set()

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

**File:** chia/wallet/trade_manager.py (L1046-1056)
```python

            return {
                asset_id: (
                    [
                        dataclasses.replace(
                            payment,
                            puzzle_hash=construct_pending_approval_state(
                                payment.puzzle_hash, payment.amount
                            ).get_tree_hash(),
                        )
                        for payment in payments
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

**File:** chia/wallet/puzzles/clawback/drivers.py (L100-135)
```python
def create_merkle_puzzle(timelock: uint64, sender_ph: bytes32, recipient_ph: bytes32) -> Program:
    merkle_tree = create_clawback_merkle_tree(timelock, sender_ph, recipient_ph)
    puzzle: Program = P2_1_OF_N.curry(merkle_tree.calculate_root())
    return puzzle


def create_merkle_solution(
    timelock: uint64,
    sender_ph: bytes32,
    recipient_ph: bytes32,
    inner_puzzle: Program,
    inner_solution: Program,
) -> Program:
    """
    Recreates the full merkle tree of a p2_1_of_n clawback coin. It uses the timelock and each party's
    puzhash to create the tree.
    The provided inner puzzle must hash to match either the sender or recipient puzhash
    If it's the sender, then create the clawback solution. If it's the recipient then create the claim
    solution.
    Returns a program which is the solution to a p2_1_of_n clawback.
    """
    merkle_tree = create_clawback_merkle_tree(timelock, sender_ph, recipient_ph)
    inner_puzzle_hash = inner_puzzle.get_tree_hash()
    if inner_puzzle_hash == sender_ph:
        cb_inner_puz = create_p2_puzzle_hash_puzzle(sender_ph)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_p2_puzzle_hash_solution(inner_puzzle, inner_solution)
    elif inner_puzzle_hash == recipient_ph:
        condition = [80, timelock]
        cb_inner_puz = create_augmented_cond_puzzle(condition, inner_puzzle)
        merkle_proof = create_merkle_proof(merkle_tree, cb_inner_puz.get_tree_hash())
        cb_inner_solution = create_augmented_cond_solution(inner_solution)
    else:
        raise ValueError("Invalid Clawback inner puzzle.")
    solution: Program = Program.to([merkle_proof, cb_inner_puz, cb_inner_solution])
    return solution
```
