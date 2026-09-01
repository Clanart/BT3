# Q3414: `validate_payer_is_operator` attributes a payout by an OP_RETURN anyone can rewrite

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction observe a broadcast payout, rebuild a conflicting transaction reusing the withdrawer's `SinglePlusAnyoneCanPay`-signed input 0 and output 0 but carrying a different (or unparsable) OP_RETURN, get it mined first, and thereby make `validate_payer_is_operator` in `core/src/operator.rs` credit the withdrawal to a party that never funded it - or to nobody at all?

## Target
- File/function: `core/src/operator.rs` -> `validate_payer_is_operator`
- Entrypoint: a Bitcoin transaction broadcast by an unprivileged party paying only mining fees -> `validate_payer_is_operator`
- Attacker controls: the replacement transaction's fee, its extra inputs, and the OP_RETURN payload; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: redirect or destroy the settlement attribution of a payout that has already left an honest party's wallet
- Invariant to test: the party credited for withdrawal index i == the party whose funds paid that payout output
- Expected Immunefi impact: High - direct loss of funds (BTC fronted by a bridge participant, or a user withdrawal that can never be settled)
- Fast validation: regtest: race two payout candidates and assert attribution follows the funder, not the OP_RETURN
