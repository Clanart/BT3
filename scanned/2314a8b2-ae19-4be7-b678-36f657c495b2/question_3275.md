# Q3275: Starknet token-id hash mapping address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public Starknet `deploy_token`` so that `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt` normalizes two distinct chain-specific addresses into the same local representation, violating `address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::deploy_token token-id hash and salt`
- Entrypoint: `public Starknet `deploy_token``
- Attacker controls: token-id bytes, token-id hash low half used as deploy salt, and preexisting mapping state
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: address derivation and token-id mapping must not let two token ids share one deployed address or one token id deploy twice under different addresses
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
