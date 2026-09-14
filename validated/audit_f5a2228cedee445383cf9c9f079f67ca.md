### Title
Anyone completing an offer can redirect the counterparty's excess "arbitrage" surplus to their own address instead of returning it - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.to_valid_spend()` requires an `arbitrage_ph` whenever a side of a trade has an amount surplus at a settlement coin (`OFFER_MOD`), and that address is supplied by whoever is completing/finalizing the spend, not tied to any check that it belongs to the party who actually deposited the surplus funds.

### Finding Description
Chia offers work by locking each side's assets into a `settlement_payments` puzzle (`OFFER_MOD`) coin, identified by `OFFER_MOD_HASH` for XCH or `construct_puzzle(driver, OFFER_MOD)` for CATs/other assets [1](#0-0) . The puzzle itself imposes no ownership check — spending it only requires supplying a solution containing a nonce and a list of `CREATE_COIN` payments; whoever produces the matching `AssertPuzzleAnnouncement`/notarized-payment solution can direct where the coin's value goes.

When an offer's requested/offered amounts don't perfectly balance to zero (e.g. because the maker over-selected coins, or a CAT wallet can't split an exact remainder), `Offer.arbitrage()` computes a surplus per asset [2](#0-1) , and `is_valid()` requires all arbitrage amounts to be non-negative [3](#0-2) . Any positive surplus must be sent somewhere, and `to_valid_spend()` appends an extra `NotarizedPayment(arbitrage_ph, uint64(arbitrage_amount))` to route it — but `arbitrage_ph` is simply passed in by the caller finishing the spend, with no validation that it's the puzzle hash of the party who actually funded the surplus: [4](#0-3) 

In `TradeManager.respond_to_offer`, the taker (`self`) is the one who calls `to_valid_spend`, and elsewhere in the trade manager the taker's own newly-derived puzzle hash (`p2_ph = await action_scope.get_puzzle_hash(...)`) is used as the destination for received funds [5](#0-4) . There is no code path here that computes `arbitrage_ph` back to the *maker* (the original offer creator) if the maker is the side that overpaid — the value of `arbitrage_ph` is entirely at the discretion of whoever assembles the final valid spend bundle. This exactly mirrors the Wido bug: funds parked in a shared, unauthenticated settlement contract, with no check tying the "leftover"/surplus destination to the party that actually deposited it, can be swept by whichever party (or any third party who observes the pending spend bundle and constructs a competing/replacing valid completion first) supplies the completing solution.

### Impact Explanation
If a maker's offer coin ends up holding more of an asset than what was promised to the taker (a realistic scenario with CAT wallets, since exact remainders can't be split without another coin, or due to a coin-selection bug/rounding), that surplus is not automatically returned to the maker on-chain. Whoever assembles/broadcasts the final completing spend — the taker, or in a race, any third party who can observe the offer file/spend-bundle and craft their own valid completion spend before the intended counterpart's transaction lands — chooses `arbitrage_ph` and can direct the surplus to themselves, resulting in unauthorized transfer of the maker's assets.

### Likelihood Explanation
Medium. It requires an offer where the offered/requested amounts don't net exactly to zero for some asset id (which the code explicitly anticipates and handles via `arbitrage()`/`is_valid()`), and it requires an attacker to observe the unconfirmed spend bundle (offer file or in-flight completion) before it's confirmed and construct a competing completion with a different `arbitrage_ph`. This is analogous to the front-running window described in the Wido report, where an attacker races a pending transaction to capture stray funds.

### Recommendation
Bind the surplus/arbitrage destination cryptographically to the offering party at offer-creation time (e.g., embed the intended change/arbitrage puzzle hash into the notarized-payment nonce or as a signed condition asserted by the maker's own coin), rather than allowing whoever finalizes `to_valid_spend()` to choose an arbitrary `arbitrage_ph`. At minimum, the wallet-level completion logic should verify that any surplus is routed back to the party whose coins produced it, not simply to whichever wallet action_scope happens to construct the final bundle.

### Proof of Concept
1. Maker creates an offer requesting CAT-B for CAT-A, selecting CAT-A coins whose total slightly exceeds the offered amount (unavoidable if their coin set can't make exact change), producing a positive `arbitrage()` value for CAT-A.
2. Maker publishes the offer file (partial spend bundle, still incomplete — `is_valid()` requires an `arbitrage_ph` to complete it).
3. An attacker observing the offer (or the taker themselves in `respond_to_offer`) constructs the final `to_valid_spend(arbitrage_ph=<attacker_address>)` call, using their own puzzle hash as `arbitrage_ph`, and broadcasts it first via `push_tx`.
4. Because `OFFER_MOD` coins impose no ownership restriction beyond a valid notarized-payment solution, the network accepts the spend, and the surplus CAT-A leftover is paid to the attacker's puzzle hash instead of returning to the maker.

### Citations

**File:** chia/wallet/trading/offer.py (L49-50)
```python
OFFER_MOD = Program.from_bytes(SETTLEMENT_PAYMENT)
OFFER_MOD_HASH = bytes32(SETTLEMENT_PAYMENT_HASH)
```

**File:** chia/wallet/trading/offer.py (L329-341)
```python
    def arbitrage(self) -> dict[bytes32 | None, int]:
        """
        Returns a dictionary of the type of each asset and amount that is involved in the trade
        With the amount being how much their offered amount within the offer
        exceeds/falls short of their requested amount.
        """
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        arbitrage_dict: dict[bytes32 | None, int] = {}
        for asset_id in [*requested_amounts.keys(), *offered_amounts.keys()]:
            arbitrage_dict[asset_id] = offered_amounts.get(asset_id, 0) - requested_amounts.get(asset_id, 0)

```

**File:** chia/wallet/trading/offer.py (L500-502)
```python
    # Validity is defined by having enough funds within the offer to satisfy both sides
    def is_valid(self) -> bool:
        return all([value >= 0 for value in self.arbitrage().values()])
```

**File:** chia/wallet/trading/offer.py (L506-522)
```python
    def to_valid_spend(self, arbitrage_ph: bytes32 | None = None, solver: Solver = Solver({})) -> WalletSpendBundle:
        if not self.is_valid():
            raise ValueError("Offer is currently incomplete")

        completion_spends: list[CoinSpend] = []
        all_offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        total_arbitrage_amount: dict[bytes32 | None, int] = self.arbitrage()
        for asset_id, payments in self.requested_payments.items():
            offered_coins: list[Coin] = all_offered_coins[asset_id]

            # Because of CAT supply laws, we must specify a place for the leftovers to go
            arbitrage_amount: int = total_arbitrage_amount[asset_id]
            all_payments: list[NotarizedPayment] = payments.copy()
            if arbitrage_amount > 0:
                assert arbitrage_amount is not None
                assert arbitrage_ph is not None
                all_payments.append(NotarizedPayment(arbitrage_ph, uint64(arbitrage_amount)))
```

**File:** chia/wallet/trade_manager.py (L507-518)
```python
                    # this is what we are receiving in the trade
                    memos: list[bytes] = []
                    p2_ph = await action_scope.get_puzzle_hash(self.wallet_state_manager)
                    if isinstance(id, int):
                        wallet_id = uint32(id)
                        wallet = self.wallet_state_manager.wallets.get(wallet_id)
                        assert isinstance(wallet, (Wallet, CATWallet))
                        if wallet.type() != WalletType.STANDARD_WALLET:
                            if callable(getattr(wallet, "get_asset_id", None)):  # ATTENTION: new wallets
                                assert isinstance(wallet, CATWallet)
                                asset_id = wallet.get_asset_id()
                                memos = [p2_ph]
```
