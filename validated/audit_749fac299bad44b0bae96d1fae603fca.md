### Title
Uncaught `IndexError` panic on empty requested-payment list during offer announcement hashing - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.calculate_announcements()` in `chia/wallet/trading/offer.py` unconditionally indexes `payments[0]` when computing the tree hash of a requested-payment announcement. An untrusted offer file (received from an offer counterparty) can be crafted so that `Offer.from_spend_bundle()`/`Offer.from_bytes()`/`Offer.from_bech32()` produces an empty `list[NotarizedPayment]` for a given asset id, which then causes an unhandled `IndexError` when the wallet processes the offer (e.g. while accepting/taking it), crashing the wallet request. This is the same bug class as CVE-2025-62370 in `alloy_dyn_abi::TypedData`: an uncaught panic triggered by indexing the first element of an attacker-controlled, potentially-empty array during hash computation used for signing/announcement data.

### Finding Description
`Offer.from_spend_bundle()` parses the "dummy" `CoinSpend`s embedded in an offer file to reconstruct `requested_payments`: [1](#0-0) 

For every coin spend whose `parent_coin_info == bytes32.zeros`, it iterates `payment_group`s inside the solution and builds `notarized_payments`, then sets `requested_payments[asset_id] = notarized_payments` — with no requirement that the resulting list be non-empty. An attacker constructing an offer can supply a solution `()` (or a solution containing a payment_group whose args iterate to zero conditions) so that this list is `[]` for some `asset_id`.

Later, when the offer is fully reconstructed (e.g. by `Offer.__init__` -> `__post_init__`, or when the wallet needs to build announcements for taking/completing the offer), `Offer.calculate_announcements()` is invoked: [2](#0-1) 

Line 145, `payments[0].nonce`, unconditionally accesses the first element of the `payments` list for each `asset_id`. If `payments` is empty (as constructed above), this raises an uncaught `IndexError`, propagating up through wallet logic (`chia/wallet/trade_manager.py` calls `calculate_announcements`) instead of being handled as a validation error like the other malformed-offer checks in `Offer.__post_init__` (e.g. duplicate-payment or missing-driver checks, which raise clean `ValueError`s).

This mirrors the alloy-dyn-abi flaw precisely: a hashing routine (`get_tree_hash()` on `(payments[0].nonce, ...)`) indexes the first element of a list that is fully attacker-controlled and can legitimately be empty, without a length check, causing an uncaught exception rather than a clean validation failure.

### Impact Explanation
Any wallet user who receives or attempts to process a maliciously crafted offer file (via `Offer.from_bech32`, `Offer.from_bytes`, or `Offer.from_compressed`, all of which are reachable from RPC endpoints like `take_offer`/`check_offer_validity` in the wallet trade flow) can trigger an unhandled `IndexError` deep in wallet processing. This is a spend-triggered/wallet-action-triggered processing halt: it can crash or hang the specific wallet RPC call handling the offer and, depending on exception propagation, disrupt the wallet service's processing of that request — a denial-of-service condition analogous to the reported `eip712_signing_hash()` panic. It does not directly enable theft or fund movement, but it satisfies the "spend-triggered transaction-processing halt" impact category from the validation rules.

### Likelihood Explanation
Likelihood is high for any wallet user who evaluates or accepts offers from untrusted counterparties (a core, expected usage pattern for the offer/trade feature). Constructing the malicious solution requires no special privileges — only crafting a coin spend with `parent_coin_info == bytes32.zeros` and a solution whose payment_group list is empty, which is fully within the attacker's control when generating an offer file to share with a victim wallet.

### Recommendation
In `Offer.calculate_announcements()`, validate that `payments` is non-empty before accessing `payments[0]`; raise a clean `ValueError`/`ValidationError` (consistent with the other malformed-offer checks in `__post_init__`) instead of allowing an `IndexError` to propagate. Additionally, consider rejecting empty payment lists for a given `asset_id` earlier, in `Offer.from_spend_bundle()`, so malformed offers are rejected at parse time rather than deep in later processing.

### Proof of Concept
Conceptual PoC (not executed, derived from code reading):
1. Construct a `WalletSpendBundle` containing one `CoinSpend` whose `coin.parent_coin_info == bytes32.zeros`, `coin.puzzle_hash` matching `OFFER_MOD.get_tree_hash()`, and `solution` = `Program.to([])` (i.e., no payment groups).
2. Serialize this bundle and encode it as an offer file (bech32/compressed), e.g. via the same mechanisms `Offer.to_bech32`/`compress` use in reverse.
3. Send this offer file to a victim wallet; when the wallet parses it via `Offer.from_bech32` -> `Offer.from_spend_bundle`, `requested_payments[None] = []` is produced.
4. When the wallet subsequently calls `Offer.calculate_announcements(self.requested_payments, self.driver_dict)` (via `chia/wallet/trade_manager.py` during offer take/accept processing), line 145's `payments[0].nonce` raises `IndexError: list index out of range`, crashing that RPC call path. [3](#0-2) [4](#0-3)

### Citations

**File:** chia/wallet/trading/offer.py (L132-148)
```python
    def calculate_announcements(
        notarized_payments: dict[bytes32 | None, list[NotarizedPayment]],
        driver_dict: dict[bytes32, PuzzleInfo],
    ) -> list[AssertPuzzleAnnouncement]:
        announcements: list[AssertPuzzleAnnouncement] = []
        for asset_id, payments in notarized_payments.items():
            if asset_id is not None:
                if asset_id not in driver_dict:
                    raise ValueError("Cannot calculate announcements without driver of requested item")
                settlement_ph: bytes32 = construct_puzzle(driver_dict[asset_id], OFFER_MOD).get_tree_hash()
            else:
                settlement_ph = OFFER_MOD_HASH

            msg: bytes32 = Program.to((payments[0].nonce, [p.as_condition_args() for p in payments])).get_tree_hash()
            announcements.append(AssertPuzzleAnnouncement(asserted_ph=settlement_ph, asserted_msg=msg))

        return announcements
```

**File:** chia/wallet/trading/offer.py (L644-657)
```python
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
                asset_id = None
            if coin_spend.coin.parent_coin_info == bytes32.zeros:
                notarized_payments: list[NotarizedPayment] = []
                for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
                    nonce = bytes32(payment_group.first().as_atom())
                    payment_args_list = payment_group.rest().as_iter()
                    notarized_payments.extend(
                        [NotarizedPayment.from_condition_and_nonce(condition, nonce) for condition in payment_args_list]
                    )

                requested_payments[asset_id] = notarized_payments
```
