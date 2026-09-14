## Title
Permanent loss of CR-CAT funds when the recipient's Verified Credential is revoked/unavailable while a payment sits in the "pending approval" state - (File: `chia/wallet/vc_wallet/cr_cat_wallet.py`, `chia/wallet/vc_wallet/cr_cat_drivers.py`)

### Summary
Credential-Restricted CATs (CR-CATs) are Chia's analog of "blacklistable" tokens: only a wallet holding a valid Verified Credential (VC) issued by one of the CAT's `authorized_providers` may move CR-CAT funds out of the "pending approval" puzzle state. If the intended recipient of a CR-CAT payment (e.g. the counterparty of an offer/trade) never obtains, loses, or is denied a valid VC/proof from an authorized provider, the funds sent to them are permanently locked with no alternative recovery path for either the sender or the recipient - the same "blacklisted counterparty" risk described in the reference report, but with a strictly worse outcome (no eventual recall, unlike the lender in Cooler.sol who can still keep the collateral).

### Finding Description
When a CR-CAT payment is created for a party who does not currently hold a valid VC, the coin is placed in a special "pending approval" inner state constructed by `construct_pending_approval_state`: [1](#0-0) 

This pending coin remains a fully credential-restricted CR-CAT (still wrapped by `CREDENTIAL_RESTRICTION`), so any future spend to move it to a real, spendable inner puzzle hash still requires satisfying the CR layer's proof-of-inclusion checks against `authorized_providers`/`proofs_checker`: [2](#0-1) 

The wallet-side claim routine `claim_pending_approval_balance` explicitly requires a VC from one of the CAT's `authorized_providers` with valid, matching proofs before it can construct a spend to move funds out of pending state; if no such VC exists, it raises and the funds simply stay pending: [3](#0-2) 

During offer/trade settlement, `VCWallet.add_vc_authorization` decides, per CR-CAT output, whether the spend is acceptable (i.e., it goes to a known wallet, back to origin as change, to the pending-approval hash, or to the offer mod); a taker/maker only authorizes CR-CAT movement using a VC they currently hold, and offers that push CR-CAT value to a counterparty without a currently-valid VC route it to the pending-approval state: [4](#0-3) [5](#0-4) 

There is no alternate spend path defined anywhere in `cr_cat_drivers.py` for a coin stuck in this pending state (no timelock-based fallback to the sender, no way for a provider-less holder to reclaim). The only exit is `CRCAT.spend_many` driven by `claim_pending_approval_balance`, which strictly requires a currently-valid VC/proof. Because credential authority (the provider) can revoke or simply fail to (re)issue proofs for a given VC/DID at any time - this is precisely the "blacklist" lever CR-CATs are designed to give issuers - a counterparty in an offer or CAT transfer who is denied or stripped of a valid VC after (or even during) a swap can never claim the CR-CAT payment that was already sent to them. Since offers are atomic settlements, the other leg of the trade (e.g., XCH, NFT, or a different CAT) has already been transferred away by that point, so the affected party has both given up their side of the trade and cannot claim what they were owed - and unlike the referenced Cooler.sol bug, no other party (not even the original sender) can recover the stuck funds either, since the CR layer's proof check gates every future spend of that coin.

### Impact Explanation
This is a real, protocol-level (Medium) impact: value can become permanently unspendable/unrecoverable as a direct consequence of a single completed spend-bundle/offer settlement, whenever the receiving side of a CR-CAT payment is (or becomes) unable to present a valid VC from an authorized provider. This mirrors the "risk using blacklistable tokens" class from the reference finding, but is worse because chia's CR-CAT design provides no fallback reclaim mechanism at all (Cooler.sol at least lets the lender keep collateral eventually).

### Likelihood Explanation
This requires use of CR-CATs specifically (a niche, permissioned CAT type intended for regulated/KYC'd assets), and requires either (a) the recipient never obtaining a matching VC/proof after the trade, or (b) the issuing provider revoking/failing to update proofs for the recipient's VC. Both are realistic operational scenarios for any real-world regulated CR-CAT deployment (KYC lapses, credential expiry, provider-side compliance actions), making this a plausible, non-adversarial-only failure mode reachable purely through ordinary offer/trade usage.

### Recommendation
- Document explicitly, at both the protocol and wallet-UX level, that sending a CR-CAT to a recipient without a currently valid, provider-issued VC risks permanent loss of funds if that VC is never granted or is revoked before the pending balance is claimed.
- Consider adding a timelock-gated fallback spend path to the "pending approval" puzzle (analogous to the clawback puzzle's `P2_1_OF_N`/merkle timelock design in `chia/wallet/puzzles/clawback/drivers.py`) so that, after a sufficiently long time-lock, the original sender (or the recipient via some non-VC path) can reclaim the coin if approval never happens.
- Warn users in the offer/trade flow (`add_vc_authorization`) when a CR-CAT payment leg is about to be routed to the pending-approval state, since this indicates the recipient currently cannot claim their funds.

### Proof of Concept
1. Maker creates an offer selling a standard asset for a CR-CAT restricted to `authorized_providers = [ProviderX]`.
2. Taker (who has no VC issued by `ProviderX`) accepts the offer; `VCWallet.add_vc_authorization` routes the CR-CAT output to the "pending approval" puzzle hash (`construct_pending_approval_state`) since the taker isn't a known VC holder yet.
3. Trade settles on-chain; taker has now given up their side of the trade and holds a CR-CAT coin in `CoinType.CRCAT_PENDING`.
4. `ProviderX` never issues (or revokes) a VC for the taker.
5. Taker calls `crcat_approve_pending` → `CRCATWallet.claim_pending_approval_balance`, which raises `RuntimeError("No VC exists that can approve spends for CR-CAT wallet ...")`, per: [6](#0-5) 
6. The CR-CAT coin remains permanently locked in the pending-approval puzzle; no code path exists to spend it without a matching VC, so the funds are unrecoverable by any party.

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

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L166-168)
```python
# For the "pending approval" state
def construct_pending_approval_state(puzzle_hash: bytes32, amount: uint64) -> Program:
    return PENDING_VC_ANNOUNCEMENT.curry(Program.to([[51, puzzle_hash, amount, [puzzle_hash]]]))
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

**File:** chia/wallet/vc_wallet/vc_wallet.py (L439-453)
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
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L494-517)
```python
            outputs_ok: bool = True
            for cc in [c for c in crcat_spend.inner_conditions if c.at("f").as_int() == 51]:
                if not (
                    (  # it's coming to us
                        await self.wallet_state_manager.get_wallet_identifier_for_puzzle_hash(
                            bytes32(cc.at("rf").as_atom())
                        )
                        is not None
                    )
                    or (  # it's going back where it came from
                        bytes32(cc.at("rf").as_atom()) == crcat_spend.crcat.inner_puzzle_hash
                    )
                    or (  # it's going to the pending state
                        cc.at("rrr") != Program.NIL
                        and cc.at("rrrf").atom is None
                        and bytes32(cc.at("rf").as_atom())
                        == construct_pending_approval_state(
                            bytes32(cc.at("rrrff").as_atom()), uint64(cc.at("rrf").as_int())
                        ).get_tree_hash()
                    )
                    or bytes32(cc.at("rf").as_atom()) == Offer.ph()  # it's going to the offer mod
                ):
                    outputs_ok = False  # pragma: no cover
            if our_crcat or outputs_ok:
```
