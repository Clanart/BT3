[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L42-49)
```text
    /// Object code deployment feature not supported.
    const EOBJECT_CODE_DEPLOYMENT_NOT_SUPPORTED: u64 = 1;
    /// Not the owner of the `code_object`
    const ENOT_CODE_OBJECT_OWNER: u64 = 2;
    /// `code_object` does not exist.
    const ECODE_OBJECT_DOES_NOT_EXIST: u64 = 3;

    const OBJECT_CODE_DEPLOYMENT_DOMAIN_SEPARATOR: vector<u8> = b"aptos_framework::object_code_deployment";
```

**File:** aptos-move/framework/aptos-framework/sources/object_code_deployment.move (L80-90)
```text
    public entry fun publish(
        publisher: &signer,
        metadata_serialized: vector<u8>,
        code: vector<vector<u8>>,
    ) {
        let publisher_address = signer::address_of(publisher);
        let object_seed = object_seed(publisher_address);
        let constructor_ref = &object::create_named_object(publisher, object_seed);
        let code_signer = &constructor_ref.generate_signer();
        code::publish_package_txn(code_signer, metadata_serialized, code);

```
