## Analysis

CVE-2021-45847 describes missing input validation in a file-format parser (3MF) that lets a crafted file crash the parsing application. The closest reachable analog in this codebase is the offer-file parser in the wallet, which is a chia-controlled binary format that any offer counterparty can hand to a victim's wallet (via `Offer.from_bech32`/`from_bytes`/`try_offer_decompression`, called from RPC endpoints such as `get_offer_summary`/`check_offer_validity`/`take_offer`).

`Offer.from_spend_bundle` (invoked by `Offer.from_bytes`, which is invoked by `Offer.parse`, `Offer.from_compressed`, and `Offer.try_offer_decompression`/`from_bech32`) rebuilds `requested_payments` by iterating the dummy coin spends' solutions and coercing an attacker-controlled CLVM atom directly into a `bytes32`: [1](#0-0) 

Specifically:
```
for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
    nonce = bytes32(payment_group.first().as_atom())
    ...
``` [2](#0-1) 

There is no length check or try/except around this coercion. `bytes32(...)` raises `ValueError` for any atom whose length isn't exactly 32 bytes, and that exception is not caught anywhere in the `from_bytes`/`from_bech32`/`try_offer_decompression` call chain: [3](#0-2) 

### Title
Unvalidated nonce length in offer-file parsing crashes wallet offer processing - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.from_spend_bundle` blindly casts the first element of each "requested payment" solution group to `bytes32` without validating its length, mirroring the CVE's pattern of a file-format parser missing input validation on an untrusted structured input, allowing a crafted offer file to crash offer parsing.

### Finding Description
Offer files are a chia-defined serialization (compressed/bech32m-encoded `WalletSpendBundle`) that one party creates and hands to another party's wallet to inspect or accept. When a wallet loads an offer via `Offer.from_bech32`, `Offer.try_offer_decompression`, or `Offer.from_bytes`, it ultimately calls `Offer.from_spend_bundle`, which iterates dummy coin-spend solutions attached at `parent_coin_info == bytes32.zeros` to reconstruct `requested_payments`: [4](#0-3) 
`payment_group.first().as_atom()` returns an arbitrary-length byte string taken directly from the attacker-supplied solution CLVM program, and it is passed straight into `bytes32(...)` with no length or type validation. This is unlike other CLVM-parsing paths in the same class (e.g. `__post_init__`, `conditions()`, `_get_offered_coins`) which wrap similar attacker-controlled parsing in `try/except Exception` to fail gracefully. There is no such guard here, and callers (`from_bytes`, `parse`, `from_compressed`, `from_bech32`, `try_offer_decompression`) do not catch the resulting `ValueError` either.

### Impact Explanation
Any offer counterparty (or anyone sharing an ".offer" file/bech32 string) can craft a payment-group solution whose first atom is not exactly 32 bytes. When the victim's wallet attempts to inspect or accept the offer (via RPC calls that end up calling `Offer.from_bech32`/`from_bytes`), the uncaught `ValueError` propagates out of the parsing routine. This halts processing of that specific offer-handling RPC call with an unhandled exception, denying the wallet user the ability to inspect/accept the crafted offer and potentially disrupting any code path that eagerly parses inbound offer files (e.g., trade-manager import flows). This is a spend/offer-triggered processing-halt bug consistent in class with the CVE (attacker-crafted file causing a parser crash), though its blast radius here is scoped to the wallet's offer-handling call, not full-node consensus.

### Likelihood Explanation
Constructing such a file is trivial: an attacker only needs to build a `WalletSpendBundle` containing one dummy `CoinSpend` (parent `bytes32.zeros`, arbitrary puzzle reveal) whose solution's first payment-group's first atom is any length other than 32 bytes, then bech32m/compress it as a normal offer file. No signature or on-chain validity is required for the wallet to attempt to parse and crash on it, since parsing happens before any spend-bundle validity check.

### Recommendation
Validate the nonce atom length (and surrounding structure) in `Offer.from_spend_bundle` before coercing it to `bytes32`, and wrap the payment-group parsing loop in the same defensive `try/except` pattern already used elsewhere in `offer.py` (e.g., in `__post_init__` and `conditions()`), converting malformed offer data into a clean `ValueError`/rejection rather than allowing an unhandled exception to escape from `from_bytes`/`from_bech32`.

### Proof of Concept
1. Build a `CoinSpend` with `coin.parent_coin_info == bytes32.zeros`, any puzzle reveal, and a solution program shaped as `[[<atom_not_32_bytes>, [...]]]` (i.e., a payment group whose nonce atom is e.g. 1 byte).
2. Wrap it in a `WalletSpendBundle([coin_spend], G2Element())`, serialize with `Offer.__bytes__`/`to_bech32`-style encoding (or directly call `WalletSpendBundle` bytes and feed to `Offer.from_bytes`).
3. Call `Offer.from_bytes(offer_bytes)` (as done by wallet RPC offer-inspection/acceptance code paths).
4. Observe `bytes32(payment_group.first().as_atom())` raise `ValueError: bytes object of size X is not 32` uncaught, crashing/erroring the offer-processing call.

### Citations

**File:** chia/wallet/trading/offer.py (L634-663)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
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
            else:
                leftover_coin_spends.append(coin_spend)

        return cls(
            requested_payments, WalletSpendBundle(leftover_coin_spends, bundle.aggregated_signature), driver_dict
        )
```

**File:** chia/wallet/trading/offer.py (L697-724)
```python
    @classmethod
    def from_bech32(cls, offer_bech32: str) -> Offer:
        _hrpgot, data = bech32_decode(offer_bech32, max_length=len(offer_bech32))
        if data is None:
            raise ValueError("Invalid Offer")
        decoded = convertbits(list(data), 5, 8, False)
        decoded_bytes = bytes(decoded)
        return cls.try_offer_decompression(decoded_bytes)

    # Methods to make this a valid Streamable member
    # We basically hijack the SpendBundle versions for most of it
    @classmethod
    def parse(cls, f: BinaryIO) -> Offer:
        parsed_bundle = parse_rust(f, WalletSpendBundle)
        return cls.from_bytes(bytes(parsed_bundle))

    def stream(self, f: BinaryIO) -> None:
        spend_bundle_bytes = self.to_spend_bundle().to_bytes()
        f.write(spend_bundle_bytes)

    def __bytes__(self) -> bytes:
        return bytes(self.to_spend_bundle())

    @classmethod
    def from_bytes(cls, as_bytes: bytes) -> Offer:
        # Because of the __bytes__ method, we need to parse the dummy CoinSpends as `requested_payments`
        bundle = WalletSpendBundle.from_bytes(as_bytes)
        return cls.from_spend_bundle(bundle)
```
