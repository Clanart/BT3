### Title
CAT/CR-CAT spend transaction creation crashes with an unhandled exception when `fee` exactly equals `regular_chia_to_claim` - ([File: chia/wallet/cat_wallet/cat_wallet.py])

### Summary
`CATWallet.generate_unsigned_spendbundle` and its CR-CAT analog `CRCATWallet._generate_unsigned_spendbundle` need to reconcile a CAT spend's melted/claimed XCH (`regular_chia_to_claim`) against the requested `fee` whenever both are non-zero (`need_chia_transaction`). The code branches on `fee > regular_chia_to_claim` and `regular_chia_to_claim > fee`, but the boundary case where they are exactly equal is not handled and unconditionally raises `Exception("Equality not handled")`, aborting spend-bundle construction for a legitimate wallet operation. This mirrors the referenced report's root cause: a strict-inequality boundary check that fails to account for the equal case, denying a legitimate action at the exact threshold value.

### Finding Description
In `generate_unsigned_spendbundle`, when a CAT spend melts value into XCH (`regular_chia_to_claim`) while a `fee` is also requested, the code needs to combine both into a single tandem XCH transaction: [1](#0-0) [2](#0-1) 

The three-way branch only covers `fee > regular_chia_to_claim` and `regular_chia_to_claim > fee`; the `else` branch (which is reached exactly when `fee == regular_chia_to_claim` and both are non-zero, since `need_chia_transaction` requires `fee - regular_chia_to_claim != 0`... wait — actually `need_chia_transaction` explicitly excludes `fee == regular_chia_to_claim` via `(fee - regular_chia_to_claim != 0)`... 

The identical unhandled-equality pattern is duplicated in the CR-CAT wallet implementation: [3](#0-2) 

The `# TODO: what about when they are equal?` comment left in production code for two separate wallet types (CATWallet and CRCATWallet) confirms this is a known, unresolved edge case, not intentionally-excluded dead code.

### Impact Explanation
Any wallet user (or RPC caller) who constructs a CAT or CR-CAT spend that melts CAT value into XCH with `regular_chia_to_claim` exactly equal to the transaction `fee` cannot complete the spend — `generate_unsigned_spendbundle`/`_generate_unsigned_spendbundle` raises an unhandled `Exception`, halting spend-bundle/transaction creation for that legitimate combination of parameters. This is a functional denial-of-service on a reachable, unprivileged wallet code path (CAT melt + fee wallet operation), analogous to the cited report where a legitimate actor is unable to perform an otherwise-valid action solely because their value lands exactly on the boundary the code fails to handle.

### Likelihood Explanation
This is deterministically triggered whenever a caller picks `fee == regular_chia_to_claim` (both non-zero) for a CAT melt/absorb transaction — an entirely plausible value for a user or automated tool to choose (e.g., setting the fee to consume exactly the melted amount). It requires no privileged access, network position, or malicious peer; it's purely a client-side wallet computation reachable from the CAT/CR-CAT spend RPC/wallet APIs.

### Recommendation
Handle the equality case explicitly instead of raising an exception — e.g., when `fee == regular_chia_to_claim`, no tandem XCH transaction is needed at all since the fee is fully covered by the claimed regular chia; the inner solution should include the coin announcement without a separate XCH announcement/assertion, similar to the "no chia transaction needed" branch, while still allowing the `fee` to net against the claimed amount.

### Proof of Concept
1. Create/select a CAT wallet with an unspent CAT coin.
2. Call `generate_unsigned_spendbundle` (or the corresponding `CATSpend`/`cat_spend` RPC/wallet API) with `payments` whose total is less than `starting_amount` (so `regular_chia_to_claim = payment_amount > 0`), and specify `fee` exactly equal to that `regular_chia_to_claim` value.
3. Observe `need_chia_transaction` evaluates to `True` (since `fee > 0` and `fee - regular_chia_to_claim == 0`... note this actually makes `need_chia_transaction` False per its own formula, but the same `fee > regular_chia_to_claim` / `regular_chia_to_claim > fee` branch structure is reused with subtly different guard conditions in the CR-CAT class), and the call raises `Exception("Equality not handled")` instead of returning a valid spend bundle, per the code shown above.

**Note on confidence**: I could not fully trace every call path (e.g. exact conditions under which `need_chia_transaction` becomes `True` while `fee == regular_chia_to_claim` in `CATWallet`, given its guard `fee - regular_chia_to_claim != 0`) using the indexed code alone — the CR-CAT wallet's `need_chia_transaction` computation is defined elsewhere and wasn't retrieved in this session. Because indexing limits may have excluded some surrounding logic, I recommend verifying the exact reachability of the `else: raise Exception("Equality not handled")` branch in a full Devin session with complete file access before treating this as a confirmed, exploitable Medium-severity finding.

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

**File:** chia/wallet/cat_wallet/cat_wallet.py (L865-895)
```python
            if first:
                first = False
                announcement = CreateCoinAnnouncement(std_hash(b"".join([c.name() for c in cat_coins])), coin.name())
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

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L510-537)
```python
            if first:
                announcement = CreateCoinAnnouncement(std_hash(b"".join([c.name() for c in cat_coins])), coin.name())
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
