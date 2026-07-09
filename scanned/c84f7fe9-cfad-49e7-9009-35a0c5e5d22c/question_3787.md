# Q3787: Starknet deploy_token address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public Starknet deploy-token entrypoint` so that `starknet/src/omni_bridge.cairo::deploy_token` normalizes two distinct chain-specific addresses into the same local representation, violating `one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token`
- Entrypoint: `public Starknet deploy-token entrypoint`
- Attacker controls: signature fields, token id `ByteArray`, name, symbol, decimals, and current class hash
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: one signed metadata payload must deploy exactly one bridge token with one stable token-id mapping and decimal model
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
