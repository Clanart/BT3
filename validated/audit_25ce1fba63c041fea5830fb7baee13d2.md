### Title
Unhandled equality case in CAT spend-bundle generation blocks legitimate fee-melt-value combinations — (File: `chia/wallet/cat_wallet/cat_wallet.py`, also `chia/wallet/vc_wallet/cr_cat_wallet.py`)

### Summary
`CATWallet.generate_unsigned_spendbundle()` and `CRCATWallet._generate_unsigned_spendbundle()` compute a `need_chia_transaction` branch that must reconcile a transaction `fee` against `regular_chia_to_claim` (XCH melted out of a CAT spend). The code explicitly handles `fee > regular_chia_to_claim` and `regular_chia_to_claim > fee`, but the equal case is left as `raise Exception("Equality not handled")`, an unhandled TODO. This mirrors the ECG `LendingTerm._partialRepay()` bug pattern: an incomplete boundary-condition check turns a legitimate, valid user operation into a forced failure.

### Finding Description
In `generate_unsigned_spendbundle`, `regular_chia_to_claim` is derived from the CAT payment/starting amounts, and `fee` is a user-supplied parameter to any CAT transaction (send, offer creation, claim, etc.): [1](#0-0) 

The three-way branch on `fee` vs. `regular_chia_to_claim` explicitly only implements two of the three possible cases: [2](#0-1) 

When `fee == regular_chia_to_claim` (and both are non-zero, so `need_chia_transaction` is `True`), execution falls into the `else` branch and raises a bare `Exception("Equality not handled")`, aborting spend-bundle construction entirely. The exact same pattern is duplicated in the CR-CAT (credential-restricted CAT) wallet path: [3](#0-2) 

This is structurally the same class of bug as the ECG finding: a partial/boundary case (`interestRepaid == 0` there, `fee == regular_chia_to_claim` here) that is a perfectly valid and reachable input combination was omitted from the handling logic, so an otherwise-valid spend a user is entitled to make is rejected — not by a puzzle/consensus rule, but by an incomplete branch in the wallet's own transaction-construction code.

### Impact Explanation
Any CAT holder (or CR-CAT/offer participant) who attempts to spend CAT coins while claiming out exactly the amount of XCH needed to cover the fee (a natural, common scenario — e.g., "melt just enough CAT-locked XCH to pay this transaction's fee") will have their `generate_signed_transaction`/`generate_unsigned_spendbundle` call raise an unhandled `Exception`, halting transaction creation. This denies the user a valid, otherwise-permitted operation purely due to missing logic, which is the "spend-triggered transaction-processing halt" class explicitly listed as acceptable impact. Because the same TODO/omission exists in both `CATWallet` and `CRCATWallet`, it affects both plain CATs and credential-restricted CATs (VC-gated flows), which broadens the affected user base to normal wallet users, offer counterparties, and VC-CAT participants.

### Likelihood Explanation
Reaching the case only requires calling the standard CAT transaction/offer-creation path with a `fee` parameter that happens to equal the melted `regular_chia_to_claim` value — no privileged access or malicious peer/node is needed, and both values are attacker/user-controlled inputs to a wallet-level API (`generate_signed_transaction`, offer creation, CAT sends). It is a straightforward, easily triggerable equality that any CLI/RPC caller assembling a CAT spend with a specific fee could hit, intentionally or not.

### Recommendation
Handle the `fee == regular_chia_to_claim` case explicitly instead of raising. When the two amounts are equal, the tandem XCH transaction and CAT announcement can be generated without needing an extra `create_tandem_xch_tx` fee-vs-claim asymmetry — e.g., treat it the same as the `fee > regular_chia_to_claim` branch (calling `create_tandem_xch_tx` with the equal amounts, since `fee - amount_to_claim == 0` is already a valid branch inside `create_tandem_xch_tx` itself), or the `regular_chia_to_claim > fee` branch. Apply the fix in both `chia/wallet/cat_wallet/cat_wallet.py` and `chia/wallet/vc_wallet/cr_cat_wallet.py`.

### Proof of Concept
1. Fund a wallet with a CAT and some XCH backing.
2. Call `CATWallet.generate_signed_transaction(amounts, puzzle_hashes, action_scope, fee=F, coins=selected_coins, ...)` where the selected CAT coins' total exceeds the payment total by exactly `F` (i.e., `payment_amount < starting_amount` and `starting_amount - payment_amount == F`), making `regular_chia_to_claim == F`.
3. Observe that instead of producing a valid `WalletSpendBundle`, the call raises `Exception("Equality not handled")` at [4](#0-3) , blocking a legitimate CAT spend that would otherwise succeed for any other fee value.

### Citations

**File:** chia/wallet/cat_wallet/cat_wallet.py (L817-824)
```python
        # Figure out if we need to absorb/melt some XCH as part of this
        regular_chia_to_claim: int = 0
        if payment_amount > starting_amount:
            fee = uint64(fee + payment_amount - starting_amount)
        elif payment_amount < starting_amount:
            regular_chia_to_claim = payment_amount

        need_chia_transaction = (fee > 0 or regular_chia_to_claim > 0) and (fee - regular_chia_to_claim != 0)
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L868-895)
```python
                if need_chia_transaction:
                    if fee > regular_chia_to_claim:
                        await self.create_tandem_xch_tx(
                            fee,
                            uint64(regular_chia_to_claim),
                            action_scope,
                            extra_conditions=(announcement.corresponding_assertion(),),
                        )
                        innersol = await self.make_inner_solution(
                            coin=coin,
                            primaries=primaries,
                            conditions=(*extra_conditions, announcement),
                        )
                    elif regular_chia_to_claim > fee:  # pragma: no cover
                        xch_announcement = await self.create_tandem_xch_tx(
                            fee,
                            uint64(regular_chia_to_claim),
                            action_scope,
                        )
                        assert xch_announcement is not None
                        innersol = await self.make_inner_solution(
                            coin=coin,
                            primaries=primaries,
                            conditions=(*extra_conditions, xch_announcement, announcement),
                        )
                    else:
                        # TODO: what about when they are equal?
                        raise Exception("Equality not handled")
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L512-537)
```python
                if need_chia_transaction:
                    if fee > regular_chia_to_claim:
                        await self.create_tandem_xch_tx(
                            fee,
                            uint64(regular_chia_to_claim),
                            action_scope,
                            extra_conditions=(announcement.corresponding_assertion(),),
                        )
                        innersol = self.standard_wallet.make_solution(
                            primaries=primaries,
                            conditions=(*extra_conditions, announcement),
                        )
                    elif regular_chia_to_claim > fee:
                        xch_announcement = await self.create_tandem_xch_tx(
                            fee,
                            uint64(regular_chia_to_claim),
                            action_scope,
                        )
                        assert xch_announcement is not None
                        innersol = self.standard_wallet.make_solution(
                            primaries=primaries,
                            conditions=(*extra_conditions, xch_announcement, announcement),
                        )
                    else:
                        # TODO: what about when they are equal?
                        raise Exception("Equality not handled")
```
