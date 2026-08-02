[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** third_party/move/move-vm/runtime/src/storage/environment.rs (L24-29)
```rust
use move_core_types::{
    account_address::AccountAddress,
    identifier::{IdentStr, Identifier},
    language_storage::{ModuleId, TypeTag, MEM_MODULE_ID, OPTION_MODULE_ID},
    vm_status::{sub_status::unknown_invariant_violation::EPARANOID_FAILURE, StatusCode},
};
```

**File:** third_party/move/move-vm/runtime/src/storage/environment.rs (L433-448)
```rust
    pub fn get_module_bytes_override(
        &self,
        addr: &AccountAddress,
        name: &IdentStr,
    ) -> Option<Bytes> {
        let enable_enum_option = self.vm_config().enable_enum_option;
        let enable_framework_for_option = self.vm_config().enable_framework_for_option;
        if !enable_framework_for_option && enable_enum_option {
            if addr == OPTION_MODULE_ID.address() && *name == *OPTION_MODULE_ID.name() {
                return Some(self.get_option_module_bytes());
            }
            if addr == MEM_MODULE_ID.address() && *name == *MEM_MODULE_ID.name() {
                return Some(self.get_mem_module_bytes());
            }
        }
        None
```

**File:** third_party/move/move-vm/runtime/src/storage/publishing.rs (L135-139)
```rust
        let is_enum_option_enabled = staged_runtime_environment.vm_config().enable_enum_option;
        let is_framework_for_option_enabled = staged_runtime_environment
            .vm_config()
            .enable_framework_for_option;
        let deserializer_config = &staged_runtime_environment.vm_config().deserializer_config;
```
