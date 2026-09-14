Based on the codebase, I found a valid analog to the reported Eigenlayer issue in the CR-CAT/VC flow.

### Title
CR-CAT `authorized_providers` is immutably curried into the on-chain puzzle, permanently stranding funds if all authorized DID providers become unusable - (File: chia/wallet/vc_wallet/cr_cat_drivers.py)

### Summary
CR-CATs (credential-restricted CATs) can only be spent when the spender presents a Verified Credential (VC) issued by one of a fixed set of `authorized_providers` (DIDs) that is curried into the coin's puzzle at mint time. This list is never updatable on-chain. If every DID in `authorized_providers` becomes unable to issue a valid VC for the current holder (e.g. the DID is transferred away by its owner, melted, or its VC is revoked), the CR-CAT coins become permanently unspendable, exactly mirroring the reported Eigenlayer bug class where a strategy removed from an external whitelist strands deposits with no update path.

### Finding Description
`construct_cr_layer` curries `authorized_providers` and `proofs_checker` directly into the CR layer puzzle at creation time: [1](#0-0) 

This list is set once when the CR-CAT wallet/coin is created and stored in `CRCATInfo`: [2](#0-1) 

There is no method anywhere in `CRCATWallet` or `CRCAT` to change `authorized_providers` for an existing coin — it is baked into the coin's puzzle hash forever (`construct_puzzle`/`construct_cr_layer` always reuse `self.authorized_providers`): [3](#0-2) 

Every CR-CAT spend requires locating a VC whose `proof_provider` is in that exact `authorized_providers` list: [4](#0-3) 

and the outer puzzle driver hard-fails if no such VC/provider can be supplied (`"Hack for similar reasons... we need a valid provider"` fallback only works when a VC is available): [5](#0-4) 

The DID acting as a provider is itself a freely transferable/spendable singleton — its owner can call `transfer_did` to send it away, or `revoke_vc`/`activate_backdoor` to revoke the VC used to prove membership: [6](#0-5) [7](#0-6) 

Once a DID that was one of (or the only) `authorized_providers` for a set of CR-CATs is transferred away from anyone willing/able to issue a fresh VC to the current CR-CAT holders, or all VCs it issued are revoked, there is no on-chain mechanism to add a new authorized provider or otherwise loosen the restriction — the coin's puzzle hash is fixed. This is structurally identical to the Eigenlayer report: an external authority (there, the strategy whitelister; here, the DID owner controlling provider status) can unilaterally invalidate the only path to move funds, and the affected protocol layer (`RioLRTAssetRegistry` there; the CR-CAT puzzle/`CRCATInfo` here) has no update function to remap the dependency.

### Impact Explanation
Impact is High: CR-CAT balances held by any wallet can become permanently locked with no possible recovery path once all `authorized_providers` are unable/unwilling to issue new VCs to the current holders. Unlike a temporary liquidity freeze, this is unbounded — since the restriction is embedded in the coin's puzzle hash, there is no protocol-level remediation (no equivalent of "re-whitelisting" is possible from the coin holder's side).

### Likelihood Explanation
Likelihood is realistic in the intended use case of CR-CATs (e.g. KYC/accredited-investor gated tokens), where the token issuer explicitly wants to control who can hold/move the asset via one or a small number of DIDs. Because DID ownership/transfer and VC issuance/revocation are fully within the DID owner's unilateral control (`transfer_did`, `activate_backdoor`), and are independent actions from the CR-CAT holder's perspective, a holder's funds can become stuck through an action they do not control and cannot appeal on-chain, mirroring exactly the "third party revokes an external dependency, no update path exists" bug class from the source report.

### Recommendation
Consider providing an on-chain mechanism analogous to the reported fix suggestion (`setAssetStrategy`) for CR-CATs — e.g., allow the current `authorized_providers`/`proofs_checker` set to be migrated to a new provider set via a spend authorized by an existing valid VC/provider (a "provider rotation" condition), rather than requiring the restriction to be permanently fixed at mint time. Alternatively, document prominently that CR-CAT holders bear indefinite counterparty risk on every DID in `authorized_providers` for the life of the coin, since there is no fallback if all such DIDs stop cooperating.

### Proof of Concept
1. Issuer mints CR-CATs to Alice with `authorized_providers = [DID_A]` and some `proofs_checker`, per `mint_cr_cat`/`CRCATWallet.get_or_create_wallet_for_cat` flow (`chia/_tests/wallet/vc_wallet/test_vc_wallet.py:69-148`, `chia/wallet/vc_wallet/cr_cat_wallet.py:175-194`).
2. Alice receives a VC issued by `DID_A` and proofs matching `proofs_checker`.
3. The controller of `DID_A` calls `transfer_did` to send the DID to an unrelated address, or calls `vc_revoke`/`activate_backdoor` on the VC previously issued to Alice, and refuses to issue Alice a new VC (`chia/wallet/vc_wallet/vc_wallet.py:376-410`, `chia/wallet/vc_wallet/vc_drivers.py:757-799`).
4. Alice now cannot produce a valid `(proof_of_inclusions, ..., provider_id, vc_launcher_id, vc_inner_puzhash)` solution for `solve_cr_layer`, since no VC in her control has `proof_provider` matching any entry of the immutable `authorized_providers` list curried into her CR-CAT coins (`chia/wallet/vc_wallet/cr_cat_drivers.py:143-163`, `chia/wallet/vc_wallet/cr_cat_wallet.py:466-493`).
5. Because `authorized_providers` cannot be changed for existing coins, Alice's CR-CAT balance is permanently unspendable.

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

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L273-285)
```python
    def construct_puzzle(self, inner_puzzle: Program) -> Program:
        return construct_cat_puzzle(
            CAT_MOD,
            self.tail_hash,
            self.construct_cr_layer(inner_puzzle),
        )

    def construct_cr_layer(self, inner_puzzle: Program) -> Program:
        return construct_cr_layer(
            self.authorized_providers,
            self.proofs_checker,
            inner_puzzle,
        )
```

**File:** chia/wallet/cat_wallet/cat_info.py (L47-51)
```python
@streamable
@dataclass(frozen=True)
class CRCATInfo(CATInfo):
    authorized_providers: list[bytes32]
    proofs_checker: ProofsChecker
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L489-493)
```python
        for coin in coin_records:
            if vc is None:
                vc = await vc_wallet.get_vc_with_provider_in_and_proofs(
                    self.info.authorized_providers, self.info.proofs_checker.flags
                )
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L79-101)
```python
    def solve(self, constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
        coin_bytes: bytes = solver["coin"]
        coin = Coin(bytes32(coin_bytes[0:32]), bytes32(coin_bytes[32:64]), uint64.from_bytes(coin_bytes[64:72]))
        coin_name: str = coin.name().hex()
        if "vc_authorizations" in solver.info:
            vc_info: tuple[Program, Program, bytes32, bytes32 | None, bytes32 | None] = tuple(
                solver["vc_authorizations"][coin_name]
            )
        else:
            proofs_checker = UnknownPuzzle(known_program=constructor["proofs_checker"])
            assert proofs_checker.curried_args is not None
            vc_info = (
                # TODO: This is something of a hack here, doesn't really work for proofs checkers generally.
                # The problem is that the CAT driver above us is running its inner puzzle (us) in order to get the
                # conditions that are output. This is bad practice on the CAT driver's part, the protocol should support
                # asking inner drivers for what conditions they return. Alas, since this is not supported, we have to
                # do a hack that we know will work for the one known proof checker we currently have.
                proofs_checker.curried_args[0],
                Program.NIL,
                constructor["authorized_providers"][0],  # Hack for similar reasons as above, we need a valid provider
                None,
                None,
            )
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

**File:** chia/wallet/vc_wallet/vc_drivers.py (L757-799)
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
